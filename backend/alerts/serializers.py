from rest_framework import serializers
from alerts.models import EmergencyCallRecord, EmergencyRecipient, IoTDevice, validate_e164_phone
from incidents.models import Incident


class EmergencyRecipientSerializer(serializers.ModelSerializer):
    masked_phone = serializers.ReadOnlyField()

    class Meta:
        model = EmergencyRecipient
        fields = [
            "id",
            "name",
            "phone_number",
            "masked_phone",
            "role_category",
            "region",
            "severity_threshold",
            "is_active",
            "is_verified",
            "consent_received",
            "opt_out",
            "notes",
            "created_at",
        ]

    def validate_phone_number(self, value):
        cleaned = value.strip().replace(" ", "").replace("-", "")
        validate_e164_phone(cleaned)
        return cleaned


class EmergencyCallRecordSerializer(serializers.ModelSerializer):
    incident_type = serializers.CharField(source="incident.incident_type", read_only=True)
    road_name = serializers.CharField(source="incident.road_name", read_only=True)
    recipient_name = serializers.CharField(source="recipient.name", read_only=True)

    class Meta:
        model = EmergencyCallRecord
        fields = [
            "id",
            "call_sid",
            "incident",
            "incident_type",
            "road_name",
            "recipient_name",
            "phone_number_masked",
            "status",
            "attempt_count",
            "error_category",
            "error_message",
            "trigger_source",
            "created_at",
            "completed_at",
            "duration_seconds",
        ]


class TriggerCallSerializer(serializers.Serializer):
    incident_id = serializers.IntegerField(required=False, allow_null=True)
    incident_type = serializers.CharField(required=False, default="road_closure")
    road_name = serializers.CharField(required=False, allow_blank=True, default="")
    severity = serializers.CharField(required=False, default="severe")
    recipient_group = serializers.ChoiceField(
        choices=["all", "authority", "emergency_services", "transport_manager", "custom"],
        default="all",
    )
    recipient_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    custom_instructions = serializers.CharField(required=False, allow_blank=True, default="")
    alternative_route = serializers.CharField(required=False, allow_blank=True, default="")
    ignore_cooldown = serializers.BooleanField(required=False, default=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default="")


class SensorBlockageSerializer(serializers.Serializer):
    distance_cm = serializers.FloatField(required=True)
    duration_seconds = serializers.IntegerField(required=False, default=0)
    confidence_score = serializers.FloatField(required=False, default=0.0)
