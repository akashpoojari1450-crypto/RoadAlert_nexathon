import logging
from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from alerts.models import BlockageSensorEvent, EmergencyCallRecord, IoTDevice
from alerts.services.dispatcher import AlertDispatcher
from incidents.models import Incident, IncidentType, Severity, VerificationLevel

logger = logging.getLogger(__name__)


class ESP32BlockageService:
    """
    Ingests and analyzes sensor readings from authenticated ESP32 roadside nodes.
    Applies multi-reading noise filtering, confidence scoring, incident lifecycle
    management, and automatic emergency voice dispatching.
    """

    @classmethod
    def process_sensor_reading(
        cls,
        device: IoTDevice,
        distance_cm: float,
        duration_seconds: int = 0,
        confidence_score: float = 0.0,
        raw_payload: dict = None,
    ) -> dict:
        """
        Processes a sensor reading.
        Safeguards:
        1. Distance must be below device.distance_threshold_cm (e.g. 120cm).
        2. Blockage must be sustained or observed across consecutive intervals.
        3. Rate limits / cooldowns prevent spamming calls for the same blockage.
        """
        now = timezone.now()
        device.last_heartbeat = now

        # Determine if reading is currently indicating an obstruction
        is_obstructed = (distance_cm > 0) and (distance_cm <= device.distance_threshold_cm)

        if is_obstructed:
            device.consecutive_count += 1
        else:
            # Reset counter on clear road reading
            device.consecutive_count = 0

        device.save(update_fields=["last_heartbeat", "consecutive_count"])

        # Create audit event record
        event = BlockageSensorEvent.objects.create(
            device=device,
            distance_cm=round(distance_cm, 1),
            duration_seconds=duration_seconds,
            confidence_score=round(confidence_score, 1),
            is_blockage=is_obstructed,
            processed=False,
            notes=f"Consecutive={device.consecutive_count}/{device.consecutive_required}",
        )

        # Multi-sample confidence threshold:
        # Requires consecutive positive readings AND/OR minimum sustained duration
        is_verified_blockage = is_obstructed and (
            device.consecutive_count >= device.consecutive_required or duration_seconds >= 20
        )

        if not is_verified_blockage:
            logger.info(
                "Device %s: Obstruction reading logged (dist=%.1fcm, count=%d/%d). Not yet verified.",
                device.device_id,
                distance_cm,
                device.consecutive_count,
                device.consecutive_required,
            )
            return {
                "verified_blockage": False,
                "event_id": event.id,
                "consecutive_count": device.consecutive_count,
                "required_count": device.consecutive_required,
                "message": "Reading logged. Multi-reading verification in progress.",
            }

        # 3. Incident Lifecycle: Find or create active Incident
        with transaction.atomic():
            # Check for existing active incident on this road within recent window
            recent_threshold = now - timedelta(minutes=device.cooldown_minutes)
            existing_incident = (
                Incident.objects.filter(
                    road_name=device.road_name,
                    resolved_at__isnull=True,
                    started_at__gte=recent_threshold,
                )
                .order_by("-started_at")
                .first()
            )

            if existing_incident:
                incident = existing_incident
                # Elevate verification level if needed
                if incident.verification_level < VerificationLevel.GPS_CONFIRMED:
                    incident.verification_level = VerificationLevel.GPS_CONFIRMED
                incident.report_count += 1
                incident.save(update_fields=["verification_level", "report_count", "updated_at"])
                logger.info("Associated blockage event %s with active incident #%s", event.id, incident.id)
            else:
                incident = Incident.objects.create(
                    incident_type=IncidentType.ROAD_CLOSURE,
                    road_name=device.road_name or f"Sensor Sector {device.device_id}",
                    latitude=device.latitude or 12.9141,
                    longitude=device.longitude or 74.8560,
                    severity=Severity.SEVERE,
                    verification_level=VerificationLevel.GPS_CONFIRMED,
                    report_count=1,
                    confidence=85,
                    estimated_delay_min=25,
                    started_at=now,
                )
                logger.info("Created new Severe Incident #%s from ESP32 %s", incident.id, device.device_id)

            event.incident = incident
            event.processed = True
            event.save(update_fields=["incident", "processed"])

        # 4. Trigger Voice Alerts with Cooldown protection
        dispatch_result = AlertDispatcher.dispatch_for_incident(
            incident=incident,
            trigger_source=EmergencyCallRecord.TriggerSource.ESP32,
            custom_instructions=f"Automated detection from roadside sensor node {device.name}. Road obstructed.",
        )

        return {
            "verified_blockage": True,
            "event_id": event.id,
            "incident_id": incident.id,
            "road_name": incident.road_name,
            "dispatch_result": dispatch_result,
        }
