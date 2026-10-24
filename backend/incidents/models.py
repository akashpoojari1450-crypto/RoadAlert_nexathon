from django.conf import settings
from django.db import models


class IncidentType(models.TextChoices):
    ACCIDENT = "accident", "Accident"
    ROAD_REPAIR = "road_repair", "Road repair / construction"
    FESTIVAL = "festival", "Festival / public event"
    STRIKE = "strike", "Strike / protest"
    VIP_MOVEMENT = "vip_movement", "VIP movement"
    LANDSLIDE = "landslide", "Landslide"
    HEAVY_RAIN = "heavy_rain", "Heavy rain"
    FLOOD = "flood", "Flood / waterlogging"
    SIGNAL_FAILURE = "signal_failure", "Traffic signal failure"
    BREAKDOWN = "breakdown", "Vehicle breakdown"
    ROAD_CLOSURE = "road_closure", "Road closure"
    MAINTENANCE = "maintenance", "Bridge/road maintenance"
    POLICE_RESTRICTION = "police_restriction", "Police/security restrictions"
    HEAVY_TRAFFIC = "heavy_traffic", "Heavy traffic"
    UNKNOWN = "unknown", "Unknown reason"


class Severity(models.TextChoices):
    NORMAL = "normal", "Normal"
    MODERATE = "moderate", "Moderate"
    HEAVY = "heavy", "Heavy"
    SEVERE = "severe", "Severe congestion"
    CLOSED = "closed", "Road closed"


class VerificationLevel(models.IntegerChoices):
    USER_REPORTED = 1, "User reported"
    GPS_CONFIRMED = 2, "GPS confirms location"
    MULTIPLE_USERS = 3, "Multiple users report same incident"
    SPEED_CONFIRMED = 4, "GPS speed data confirms congestion"
    MEDIA_EVIDENCE = 5, "Photo/video evidence"
    AUTHORITY_CONFIRMED = 6, "Government/traffic authority confirmation"


class Incident(models.Model):
    """A real-world disruption, built up from one or more user reports."""

    incident_type = models.CharField(max_length=30, choices=IncidentType.choices)
    road_name = models.CharField(max_length=200, blank=True)  # e.g. "NH-66 - Thokkotu"
    latitude = models.FloatField()
    longitude = models.FloatField()
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MODERATE)
    verification_level = models.PositiveSmallIntegerField(
        choices=VerificationLevel.choices, default=VerificationLevel.USER_REPORTED
    )
    report_count = models.PositiveIntegerField(default=0)
    estimated_delay_min = models.PositiveIntegerField(default=0)
    confidence = models.PositiveSmallIntegerField(default=0)  # 0-100
    authority_confirmed = models.BooleanField(default=False)

    started_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["started_at"]),
        ]

    @property
    def is_active(self):
        return self.resolved_at is None

    def __str__(self):
        return f"{self.get_incident_type_display()} @ {self.road_name or (self.latitude, self.longitude)}"


class IncidentReport(models.Model):
    """One user's report. Location and time are attached automatically."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="reports"
    )
    incident_type = models.CharField(max_length=30, choices=IncidentType.choices)
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed_kmh = models.FloatField(null=True, blank=True)  # reporter's speed at report time
    description = models.TextField(blank=True)
    photo = models.ImageField(upload_to="reports/photos/", null=True, blank=True)
    video = models.FileField(upload_to="reports/videos/", null=True, blank=True)
    voice_note = models.FileField(upload_to="reports/voice/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
