from django.contrib import admin
from alerts.models import AlertCooldown, BlockageSensorEvent, EmergencyCallRecord, EmergencyRecipient, IoTDevice


@admin.register(EmergencyRecipient)
class EmergencyRecipientAdmin(admin.ModelAdmin):
    list_display = ("name", "masked_phone", "role_category", "region", "is_active", "is_verified", "consent_received", "opt_out")
    list_filter = ("role_category", "is_active", "is_verified", "consent_received", "opt_out")
    search_fields = ("name", "phone_number", "region")


@admin.register(IoTDevice)
class IoTDeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "name", "road_name", "distance_threshold_cm", "consecutive_required", "consecutive_count", "is_active", "last_heartbeat")
    list_filter = ("is_active",)
    search_fields = ("device_id", "name", "road_name")


@admin.register(EmergencyCallRecord)
class EmergencyCallRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "call_sid", "phone_number_masked", "incident", "status", "trigger_source", "created_at", "duration_seconds")
    list_filter = ("status", "trigger_source", "created_at")
    search_fields = ("call_sid", "phone_number_masked", "destination_phone_raw")
    readonly_fields = ("created_at", "updated_at", "completed_at")


@admin.register(BlockageSensorEvent)
class BlockageSensorEventAdmin(admin.ModelAdmin):
    list_display = ("id", "device", "distance_cm", "duration_seconds", "confidence_score", "is_blockage", "processed", "incident", "created_at")
    list_filter = ("is_blockage", "processed", "created_at")
    search_fields = ("device__device_id", "device__name")


@admin.register(AlertCooldown)
class AlertCooldownAdmin(admin.ModelAdmin):
    list_display = ("incident", "last_dispatched_at", "calls_dispatched_count", "cooldown_minutes")
