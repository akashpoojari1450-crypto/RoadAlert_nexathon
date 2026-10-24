import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from django.db import models, transaction
from django.utils import timezone

from alerts.models import AlertCooldown, EmergencyCallRecord, EmergencyRecipient
from alerts.services.twilio_service import TwilioVoiceService

logger = logging.getLogger(__name__)

# Reusable executor for non-blocking asynchronous call dispatching
_dispatch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="RoadAlertVoiceWorker")


def _execute_single_call(record_id: int):
    """
    Worker task: processes an individual call record from database.
    Catches all exceptions to ensure worker longevity.
    """
    try:
        record = EmergencyCallRecord.objects.select_related("incident", "recipient").get(id=record_id)
    except EmergencyCallRecord.DoesNotExist:
        logger.error("Call record %s not found for execution.", record_id)
        return

    # Check if already processed
    if record.status not in (EmergencyCallRecord.CallStatus.QUEUED, EmergencyCallRecord.CallStatus.INITIATED):
        logger.info("Call %s already in state %s; skipping duplicate dispatch.", record.id, record.status)
        return

    service = TwilioVoiceService()
    phone_to_call = record.destination_phone_raw or (record.recipient.phone_number if record.recipient else "")

    if not phone_to_call:
        record.status = EmergencyCallRecord.CallStatus.FAILED
        record.error_category = "NO_PHONE_NUMBER"
        record.error_message = "No valid destination phone number found."
        record.save(update_fields=["status", "error_category", "error_message", "updated_at"])
        return

    result = service.trigger_voice_call(
        to_phone=phone_to_call,
        speech_text=record.twiml_message,
        record_id=record.id,
        idempotency_key=record.idempotency_key,
    )

    if result.get("success"):
        record.call_sid = result.get("call_sid", "")
        # If dry run, status is simulated completed; otherwise whatever Twilio returns
        if result.get("is_dry_run"):
            record.status = EmergencyCallRecord.CallStatus.COMPLETED
            record.completed_at = timezone.now()
            record.error_message = "Dry-run simulated call."
        else:
            record.status = EmergencyCallRecord.CallStatus.INITIATED
        record.save(update_fields=["call_sid", "status", "error_message", "completed_at", "updated_at"])
        logger.info("Call record %s successfully initiated with SID %s", record.id, record.call_sid)
    else:
        record.status = EmergencyCallRecord.CallStatus.FAILED
        record.error_category = result.get("error_category", "DISPATCH_FAILED")
        record.error_message = result.get("error_message", "Unknown dispatch failure")
        record.save(update_fields=["status", "error_category", "error_message", "updated_at"])
        logger.warning(
            "Call record %s failed dispatch: %s (%s)",
            record.id,
            record.error_category,
            record.error_message,
        )


class AlertDispatcher:
    """
    Coordinates multi-recipient emergency dispatching, rate limits, cooldowns,
    idempotency, and async worker queueing.
    """

    @classmethod
    def dispatch_for_incident(
        cls,
        incident,
        recipients=None,
        trigger_source=EmergencyCallRecord.TriggerSource.MANUAL,
        custom_instructions="",
        alternative_route="",
        dispatched_by=None,
        idempotency_key=None,
        ignore_cooldown=False,
    ) -> dict:
        """
        Creates CallRecords and queues async calls for an incident.
        Returns immediate summary response dictionary.
        """
        # 1. Cooldown & Rate Limiting Check
        cooldown, _ = AlertCooldown.objects.get_or_create(
            incident=incident,
            defaults={"last_dispatched_at": timezone.now(), "calls_dispatched_count": 0},
        )

        if not ignore_cooldown and cooldown.calls_dispatched_count > 0 and cooldown.is_cooling_down():
            remaining = cooldown.remaining_cooldown_seconds()
            logger.info("Incident #%s dispatch blocked by cooldown (%ss remaining)", incident.id, remaining)
            return {
                "success": False,
                "error": "COOLDOWN_ACTIVE",
                "message": f"Emergency alert recently dispatched for this incident. Cooldown active for {remaining} more seconds.",
                "remaining_seconds": remaining,
                "queued_calls": 0,
            }

        # 2. Select Eligible Recipients
        if recipients is None:
            # Match recipients based on severity and region
            recipients = EmergencyRecipient.objects.filter(
                is_active=True,
                is_verified=True,
                consent_received=True,
                opt_out=False,
            )
            # Filter by region if specified
            if incident.road_name:
                recipients = recipients.filter(
                    models.Q(region="all") | models.Q(region__icontains=incident.road_name)
                )

        if not recipients.exists():
            return {
                "success": False,
                "error": "NO_ELIGIBLE_RECIPIENTS",
                "message": "No verified active emergency recipients found matching criteria.",
                "queued_calls": 0,
            }

        # 3. Generate Speech Text
        service = TwilioVoiceService()
        speech_text = service.build_emergency_speech_text(
            incident_type=incident.incident_type,
            road_name=incident.road_name,
            severity=incident.severity,
            detection_time=incident.started_at,
            alternative_route=alternative_route,
            instructions=custom_instructions,
        )

        queued_records = []
        base_idempotency = idempotency_key or str(uuid.uuid4())

        # 4. Atomic record creation
        with transaction.atomic():
            for rec in recipients:
                # Deduplication: check if active call in progress for this recipient and incident
                recent_call = EmergencyCallRecord.objects.filter(
                    incident=incident,
                    recipient=rec,
                    status__in=[
                        EmergencyCallRecord.CallStatus.QUEUED,
                        EmergencyCallRecord.CallStatus.INITIATED,
                        EmergencyCallRecord.CallStatus.RINGING,
                        EmergencyCallRecord.CallStatus.IN_PROGRESS,
                    ],
                ).first()

                if recent_call:
                    logger.info("Skipping recipient %s: already has active call record %s", rec.id, recent_call.id)
                    continue

                call_record = EmergencyCallRecord.objects.create(
                    incident=incident,
                    recipient=rec,
                    phone_number_masked=rec.masked_phone,
                    destination_phone_raw=rec.phone_number,
                    status=EmergencyCallRecord.CallStatus.QUEUED,
                    trigger_source=trigger_source,
                    idempotency_key=f"{base_idempotency}_{rec.id}",
                    twiml_message=speech_text,
                    dispatched_by=dispatched_by,
                )
                queued_records.append(call_record)

            # Update Cooldown tracking
            cooldown.last_dispatched_at = timezone.now()
            cooldown.calls_dispatched_count += len(queued_records)
            cooldown.save(update_fields=["last_dispatched_at", "calls_dispatched_count"])

        # 5. Non-blocking Async Dispatching
        for record in queued_records:
            _dispatch_pool.submit(_execute_single_call, record.id)

        logger.info(
            "Dispatched %d calls for Incident #%s (Trigger=%s)",
            len(queued_records),
            incident.id,
            trigger_source,
        )

        return {
            "success": True,
            "message": f"Successfully queued {len(queued_records)} emergency voice call(s).",
            "incident_id": incident.id,
            "queued_calls": len(queued_records),
            "records": [
                {
                    "record_id": r.id,
                    "recipient_name": r.recipient.name if r.recipient else "Contact",
                    "phone_masked": r.phone_number_masked,
                    "status": r.status,
                }
                for r in queued_records
            ],
            "speech_preview": speech_text,
        }
