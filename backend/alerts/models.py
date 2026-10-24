import re
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def validate_e164_phone(value):
    """
    Validates that a phone number adheres to the E.164 standard:
    '+' followed by 8 to 15 digits.
    """
    if not value:
        raise ValidationError("Phone number cannot be empty.")
    cleaned = value.strip().replace(" ", "").replace("-", "")
    if not re.match(r"^\+[1-9]\d{7,14}$", cleaned):
        raise ValidationError(
            f"'{value}' is not a valid E.164 phone number. Format must be +[country_code][number], e.g., +919876543210."
        )


def mask_phone_number(phone):
    """Masks a phone number for privacy, e.g., +91 9876543210 -> +91 98****3210"""
    if not phone:
        return ""
    p = str(phone).strip()
    if len(p) <= 6:
        return "*" * len(p)
    return p[:5] + "****" + p[-4:]


class EmergencyRecipient(models.Model):
    """
    Authorized contact who can receive automated emergency voice calls.
    Links optionally to an existing User profile to avoid duplication.
    """

    class RoleCategory(models.TextChoices):
        AUTHORITY = "authority", "Traffic Authority / Police"
        EMERGENCY_SERVICES = "emergency_services", "Emergency Services (Ambulance/Fire)"
        TRANSPORT_MANAGER = "transport_manager", "Logistics / Transport Manager"
        CITIZEN_RESPONDER = "citizen_responder", "Citizen First Responder"
        GENERAL = "general", "General Emergency Contact"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_contacts",
    )
    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=25, validators=[validate_e164_phone])
    role_category = models.CharField(
        max_length=30, choices=RoleCategory.choices, default=RoleCategory.AUTHORITY
    )
    region = models.CharField(
        max_length=100,
        blank=True,
        default="all",
        help_text="Geographic coverage or road name (e.g., 'Mangaluru', 'NH-66', or 'all')",
    )
    severity_threshold = models.CharField(
        max_length=20,
        default="heavy",
        choices=[
            ("normal", "Normal / Low"),
            ("moderate", "Moderate"),
            ("heavy", "Heavy"),
            ("severe", "Severe"),
            ("closed", "Road Closed"),
        ],
        help_text="Minimum incident severity required to call this recipient.",
    )
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(
        default=True,
        help_text="Has this phone number been verified for emergency notifications?",
    )
    consent_received = models.BooleanField(
        default=True,
        help_text="User explicit consent for automated emergency phone calls.",
    )
    opt_out = models.BooleanField(
        default=False,
        help_text="Recipient has opted out of voice calls.",
    )
    notes = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Emergency Recipient"
        verbose_name_plural = "Emergency Recipients"

    def clean(self):
        if self.phone_number:
            self.phone_number = self.phone_number.strip().replace(" ", "").replace("-", "")
            validate_e164_phone(self.phone_number)

    @property
    def masked_phone(self):
        return mask_phone_number(self.phone_number)

    @property
    def can_receive_calls(self):
        return self.is_active and self.is_verified and self.consent_received and not self.opt_out

    def __str__(self):
        return f"{self.name} ({self.masked_phone}) - {self.get_role_category_display()}"


class IoTDevice(models.Model):
    """
    Authenticated roadside IoT sensor node (e.g. ESP32 with ultrasonic/camera).
    """

    device_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    api_key = models.CharField(
        max_length=128,
        help_text="Secret token passed by device in X-Device-Token header.",
    )
    road_name = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Thresholds to avoid false positives
    distance_threshold_cm = models.FloatField(
        default=120.0,
        help_text="Obstacle distance in cm below which an obstruction is flagged.",
    )
    consecutive_required = models.PositiveSmallIntegerField(
        default=3,
        help_text="Consecutive positive readings required before triggering an alert.",
    )
    cooldown_minutes = models.PositiveIntegerField(
        default=20,
        help_text="Minutes to suppress repeat alerts from this sensor for the same blockage.",
    )

    last_heartbeat = models.DateTimeField(null=True, blank=True)
    consecutive_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} [{self.device_id}] @ {self.road_name or 'Unknown Road'}"


class BlockageSensorEvent(models.Model):
    """
    Raw and analyzed event submitted by an authenticated ESP32 device.
    """

    device = models.ForeignKey(IoTDevice, on_delete=models.CASCADE, related_name="sensor_events")
    distance_cm = models.FloatField()
    reading_count = models.PositiveIntegerField(default=1)
    duration_seconds = models.PositiveIntegerField(
        default=0, help_text="Duration the obstruction has been sustained in seconds."
    )
    confidence_score = models.FloatField(default=0.0)
    is_blockage = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    incident = models.ForeignKey(
        "incidents.Incident",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="iot_events",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Event {self.id} from {self.device.device_id} ({self.distance_cm} cm - Blockage={self.is_blockage})"


class EmergencyCallRecord(models.Model):
    """
    Immutable audit and tracking record for every voice call dispatched.
    """

    class CallStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        INITIATED = "initiated", "Initiated"
        RINGING = "ringing", "Ringing"
        IN_PROGRESS = "in-progress", "In Progress"
        COMPLETED = "completed", "Completed"
        BUSY = "busy", "Busy"
        FAILED = "failed", "Failed"
        NO_ANSWER = "no-answer", "No Answer"
        CANCELED = "canceled", "Canceled"

    class TriggerSource(models.TextChoices):
        MANUAL = "manual", "Manual Authority Dashboard"
        ESP32 = "esp32", "ESP32 Road Sensor Automated"
        API = "api", "Authorized API Trigger"

    incident = models.ForeignKey(
        "incidents.Incident",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="voice_calls",
    )
    recipient = models.ForeignKey(
        EmergencyRecipient,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="call_history",
    )
    phone_number_masked = models.CharField(max_length=30)
    destination_phone_raw = models.CharField(max_length=30, blank=True)
    call_sid = models.CharField(max_length=64, blank=True, db_index=True)
    status = models.CharField(
        max_length=30, choices=CallStatus.choices, default=CallStatus.QUEUED, db_index=True
    )
    attempt_count = models.PositiveSmallIntegerField(default=1)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    error_category = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    trigger_source = models.CharField(
        max_length=30, choices=TriggerSource.choices, default=TriggerSource.MANUAL
    )
    idempotency_key = models.CharField(max_length=128, blank=True, db_index=True)
    twiml_message = models.TextField(blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatched_emergency_calls",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Emergency Call Record"
        verbose_name_plural = "Emergency Call Records"

    def __str__(self):
        return f"Call {self.call_sid or self.id} -> {self.phone_number_masked} [{self.status}]"


class AlertCooldown(models.Model):
    """
    Prevents duplicate blast calls for the same active road blockage or incident.
    """

    incident = models.OneToOneField(
        "incidents.Incident", on_delete=models.CASCADE, related_name="alert_cooldown"
    )
    last_dispatched_at = models.DateTimeField(default=timezone.now)
    calls_dispatched_count = models.PositiveIntegerField(default=0)
    cooldown_minutes = models.PositiveIntegerField(default=15)

    def is_cooling_down(self):
        cooldown_end = self.last_dispatched_at + timedelta(minutes=self.cooldown_minutes)
        return timezone.now() < cooldown_end

    def remaining_cooldown_seconds(self):
        cooldown_end = self.last_dispatched_at + timedelta(minutes=self.cooldown_minutes)
        rem = (cooldown_end - timezone.now()).total_seconds()
        return max(0, int(rem))

    def __str__(self):
        return f"Cooldown for Incident #{self.incident_id} (active={self.is_cooling_down()})"
