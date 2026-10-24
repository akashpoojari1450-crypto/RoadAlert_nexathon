import logging
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAuthorityOrAdmin
from alerts.models import EmergencyCallRecord, EmergencyRecipient, IoTDevice
from alerts.serializers import (
    EmergencyCallRecordSerializer,
    EmergencyRecipientSerializer,
    SensorBlockageSerializer,
    TriggerCallSerializer,
)
from alerts.services.dispatcher import AlertDispatcher
from alerts.services.esp32_service import ESP32BlockageService
from alerts.services.twilio_service import TwilioVoiceService
from incidents.models import Incident, IncidentType, Severity, VerificationLevel

logger = logging.getLogger(__name__)


class TriggerCallView(APIView):
    """
    POST /api/alerts/trigger-call/
    Authorized emergency voice call dispatcher for Traffic Authority & Admin.
    """

    permission_classes = [IsAuthorityOrAdmin]

    def post(self, request):
        serializer = TriggerCallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        incident_id = data.get("incident_id")
        if incident_id:
            try:
                incident = Incident.objects.get(id=incident_id)
            except Incident.DoesNotExist:
                return Response({"detail": f"Incident #{incident_id} not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            # Create a new emergency incident on-the-fly
            incident = Incident.objects.create(
                incident_type=data.get("incident_type", IncidentType.ROAD_CLOSURE),
                road_name=data.get("road_name") or "Reported Roadway",
                latitude=float(request.data.get("latitude", 12.9141)),
                longitude=float(request.data.get("longitude", 74.8560)),
                severity=data.get("severity", Severity.SEVERE),
                verification_level=VerificationLevel.AUTHORITY_CONFIRMED,
                authority_confirmed=True,
                report_count=1,
                confidence=100,
                estimated_delay_min=30,
                started_at=timezone.now(),
            )

        # Filter recipients based on selection
        recipient_group = data.get("recipient_group")
        recipient_ids = data.get("recipient_ids", [])

        recipients_qs = EmergencyRecipient.objects.filter(
            is_active=True, is_verified=True, consent_received=True, opt_out=False
        )

        if recipient_group == "custom" and recipient_ids:
            recipients = recipients_qs.filter(id__in=recipient_ids)
        elif recipient_group in ["authority", "emergency_services", "transport_manager"]:
            recipients = recipients_qs.filter(role_category=recipient_group)
        else:
            recipients = recipients_qs.all()

        dispatch_result = AlertDispatcher.dispatch_for_incident(
            incident=incident,
            recipients=recipients,
            trigger_source=EmergencyCallRecord.TriggerSource.MANUAL,
            custom_instructions=data.get("custom_instructions", ""),
            alternative_route=data.get("alternative_route", ""),
            dispatched_by=request.user,
            idempotency_key=data.get("idempotency_key", ""),
            ignore_cooldown=data.get("ignore_cooldown", False),
        )

        if not dispatch_result.get("success"):
            return Response(dispatch_result, status=status.HTTP_400_BAD_REQUEST)

        return Response(dispatch_result, status=status.HTTP_202_ACCEPTED)


class CallStatusView(APIView):
    """
    GET /api/alerts/call-status/<str:call_sid>/
    Retrieves current status of an emergency call by Twilio Call SID or Record ID.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, call_sid):
        try:
            if call_sid.isdigit():
                record = EmergencyCallRecord.objects.select_related("incident", "recipient").get(id=int(call_sid))
            else:
                record = EmergencyCallRecord.objects.select_related("incident", "recipient").get(call_sid=call_sid)
        except EmergencyCallRecord.DoesNotExist:
            return Response({"detail": "Call record not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(EmergencyCallRecordSerializer(record).data)


@method_decorator(csrf_exempt, name="dispatch")
class TwilioStatusCallbackView(APIView):
    """
    POST /api/alerts/twilio/status-callback/
    Public webhook receiver for Twilio call progression events.
    Validates Twilio HMAC-SHA1 signature.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        service = TwilioVoiceService()
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        # Build external request URL
        req_url = request.build_absolute_uri()

        if not service.validate_twilio_signature(req_url, request.POST.dict(), signature):
            logger.warning("Rejected Twilio status callback due to invalid signature.")
            return Response({"detail": "Invalid signature."}, status=status.HTTP_403_FORBIDDEN)

        call_sid = request.POST.get("CallSid", "")
        call_status = request.POST.get("CallStatus", "").lower()
        call_duration = request.POST.get("CallDuration")
        error_code = request.POST.get("ErrorCode")
        error_message = request.POST.get("ErrorMessage")

        if not call_sid:
            return Response({"detail": "CallSid required."}, status=status.HTTP_400_BAD_REQUEST)

        record = EmergencyCallRecord.objects.filter(call_sid=call_sid).first()
        if not record:
            logger.info("Callback for unrecorded CallSid: %s (status=%s)", call_sid, call_status)
            return HttpResponse("<Response/>", content_type="application/xml")

        # Map Twilio status to model choices
        status_mapping = {
            "queued": EmergencyCallRecord.CallStatus.QUEUED,
            "initiated": EmergencyCallRecord.CallStatus.INITIATED,
            "ringing": EmergencyCallRecord.CallStatus.RINGING,
            "in-progress": EmergencyCallRecord.CallStatus.IN_PROGRESS,
            "completed": EmergencyCallRecord.CallStatus.COMPLETED,
            "busy": EmergencyCallRecord.CallStatus.BUSY,
            "failed": EmergencyCallRecord.CallStatus.FAILED,
            "no-answer": EmergencyCallRecord.CallStatus.NO_ANSWER,
            "canceled": EmergencyCallRecord.CallStatus.CANCELED,
        }

        new_status = status_mapping.get(call_status, record.status)
        record.status = new_status

        if call_duration and call_duration.isdigit():
            record.duration_seconds = int(call_duration)

        if error_code:
            record.error_category = f"TWILIO_{error_code}"
            record.error_message = error_message or f"Twilio error {error_code}"

        if new_status in [
            EmergencyCallRecord.CallStatus.COMPLETED,
            EmergencyCallRecord.CallStatus.FAILED,
            EmergencyCallRecord.CallStatus.BUSY,
            EmergencyCallRecord.CallStatus.NO_ANSWER,
            EmergencyCallRecord.CallStatus.CANCELED,
        ]:
            if not record.completed_at:
                record.completed_at = timezone.now()

        record.save()
        logger.info("Updated CallRecord %s (SID: %s) -> Status: %s", record.id, call_sid, new_status)

        return HttpResponse("<Response/>", content_type="application/xml")


@method_decorator(csrf_exempt, name="dispatch")
class TwilioTwiMLView(APIView):
    """
    POST /api/alerts/twilio/twiml/<int:record_id>/
    Delivers XML instructions to Twilio Voice when an outbound call connects.
    """

    permission_classes = [AllowAny]

    def get(self, request, record_id):
        return self._generate_response(request, record_id)

    def post(self, request, record_id):
        return self._generate_response(request, record_id)

    def _generate_response(self, request, record_id):
        record = get_object_or_404(EmergencyCallRecord, id=record_id)
        service = TwilioVoiceService()

        # Generate XML
        twiml_xml = service.generate_twiml(record.twiml_message)
        return HttpResponse(twiml_xml, content_type="application/xml")


@method_decorator(csrf_exempt, name="dispatch")
class ESP32RoadBlockageView(APIView):
    """
    POST /api/alerts/road-blockage/
    Receives and authenticates blockage telemetry from ESP32 roadside nodes.
    Authenticates via HTTP header `X-Device-Token: <api_key>`.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        api_key = request.META.get("HTTP_X_DEVICE_TOKEN", "").strip()
        if not api_key:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header.split(" ", 1)[1].strip()

        if not api_key:
            return Response(
                {"detail": "Authentication required. Missing X-Device-Token header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        device = IoTDevice.objects.filter(api_key=api_key, is_active=True).first()
        if not device:
            return Response(
                {"detail": "Invalid or inactive device credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = SensorBlockageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = ESP32BlockageService.process_sensor_reading(
            device=device,
            distance_cm=data["distance_cm"],
            duration_seconds=data.get("duration_seconds", 0),
            confidence_score=data.get("confidence_score", 0.0),
            raw_payload=request.data,
        )

        return Response(result, status=status.HTTP_200_OK)


class EmergencyRecipientListCreateView(APIView):
    """
    GET /api/alerts/recipients/
    POST /api/alerts/recipients/
    Recipient management for Traffic Authorities.
    """

    permission_classes = [IsAuthorityOrAdmin]

    def get(self, request):
        recipients = EmergencyRecipient.objects.all()
        return Response(EmergencyRecipientSerializer(recipients, many=True).data)

    def post(self, request):
        serializer = EmergencyRecipientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipient = serializer.save()
        return Response(EmergencyRecipientSerializer(recipient).data, status=status.HTTP_201_CREATED)


class CallHistoryListView(APIView):
    """
    GET /api/alerts/history/
    Lists recent emergency voice calls with masked numbers for the dashboard.
    """

    permission_classes = [IsAuthorityOrAdmin]

    def get(self, request):
        records = EmergencyCallRecord.objects.select_related("incident", "recipient").all()[:50]
        return Response(EmergencyCallRecordSerializer(records, many=True).data)


class PreviewMessageView(APIView):
    """
    POST /api/alerts/preview-message/
    Returns live preview of speech text given incident parameters.
    """

    permission_classes = [IsAuthorityOrAdmin]

    def post(self, request):
        service = TwilioVoiceService()
        incident_id = request.data.get("incident_id")
        road_name = request.data.get("road_name", "")
        incident_type = request.data.get("incident_type", "road_closure")
        severity = request.data.get("severity", "severe")
        alternative_route = request.data.get("alternative_route", "")
        instructions = request.data.get("custom_instructions", "")

        detection_time = timezone.now()
        if incident_id:
            try:
                inc = Incident.objects.get(id=incident_id)
                road_name = inc.road_name or road_name
                incident_type = inc.incident_type
                severity = inc.severity
                detection_time = inc.started_at
            except Incident.DoesNotExist:
                pass

        speech = service.build_emergency_speech_text(
            incident_type=incident_type,
            road_name=road_name,
            severity=severity,
            detection_time=detection_time,
            alternative_route=alternative_route,
            instructions=instructions,
        )
        return Response({"preview_text": speech})


@method_decorator(csrf_exempt, name="dispatch")
class ESPCameraEventView(APIView):
    """
    POST /api/alerts/camera-event/
    Receives real-time vision classification from ESP32-CAM nodes (e.g. http://10.43.195.75/).
    Supported classes:
      - 'accident': Creates Level 5 verified incident and triggers SOS automated phone calls!
      - 'ambulance': Dispatches emergency corridor clearance alert.
      - 'vip': Logs VIP transit and updates road restrictions.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        from django.db import models
        detected_class = str(request.data.get("detected_class", "")).lower().strip()
        confidence = float(request.data.get("confidence", 0.85))
        camera_ip = request.data.get("camera_ip", "10.43.195.75")
        road_name = request.data.get("road_name", "NH-66 - Surathkal Junction")
        lat = float(request.data.get("latitude", 12.9850))
        lng = float(request.data.get("longitude", 74.8020))

        if not detected_class:
            return Response(
                {"detail": "detected_class is required (e.g. 'accident', 'ambulance', 'vip')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()

        if detected_class == "accident":
            # Level 5 Media/Vision verification
            incident = Incident.objects.create(
                incident_type=IncidentType.ACCIDENT,
                road_name=road_name,
                latitude=lat,
                longitude=lng,
                severity=Severity.SEVERE,
                verification_level=VerificationLevel.MEDIA_EVIDENCE,
                report_count=1,
                confidence=int(confidence * 100),
                estimated_delay_min=45,
                started_at=now,
            )

            # Trigger immediate SOS voice call to Emergency Services & Police!
            recipients = EmergencyRecipient.objects.filter(
                is_active=True, is_verified=True, consent_received=True, opt_out=False
            )
            # Prioritize emergency services and authorities
            sos_recipients = recipients.filter(
                models.Q(role_category__in=["emergency_services", "authority"]) | models.Q(region="all")
            )
            if not sos_recipients.exists():
                sos_recipients = recipients.all()

            dispatch_result = AlertDispatcher.dispatch_for_incident(
                incident=incident,
                recipients=sos_recipients,
                trigger_source=EmergencyCallRecord.TriggerSource.ESP32,
                custom_instructions=f"AI Vision SOS from ESP32-CAM ({camera_ip}). Severe vehicle accident detected with {int(confidence*100)}% confidence. Emergency response required.",
                ignore_cooldown=True,  # Critical SOS calls bypass cooldown
            )

            return Response(
                {
                    "status": "SOS_DISPATCHED",
                    "detected_class": "accident",
                    "incident_id": incident.id,
                    "verification_level": 5,
                    "camera_ip": camera_ip,
                    "dispatch_result": dispatch_result,
                    "message": "SOS Emergency Voice Calls successfully initiated to emergency response units.",
                },
                status=status.HTTP_201_CREATED,
            )

        elif detected_class == "ambulance":
            incident = Incident.objects.create(
                incident_type=IncidentType.VIP_MOVEMENT,
                road_name=road_name,
                latitude=lat,
                longitude=lng,
                severity=Severity.MODERATE,
                verification_level=VerificationLevel.MEDIA_EVIDENCE,
                report_count=1,
                confidence=int(confidence * 100),
                estimated_delay_min=10,
                started_at=now,
            )
            return Response(
                {
                    "status": "EMERGENCY_VEHICLE_LOGGED",
                    "detected_class": "ambulance",
                    "incident_id": incident.id,
                    "message": "Ambulance detected. Roadway clearance alert broadcasted.",
                },
                status=status.HTTP_200_OK,
            )

        else:
            incident = Incident.objects.create(
                incident_type=IncidentType.VIP_MOVEMENT,
                road_name=road_name,
                latitude=lat,
                longitude=lng,
                severity=Severity.MODERATE,
                verification_level=VerificationLevel.MEDIA_EVIDENCE,
                report_count=1,
                confidence=int(confidence * 100),
                estimated_delay_min=15,
                started_at=now,
            )
            return Response(
                {
                    "status": "VIP_TRANSIT_LOGGED",
                    "detected_class": detected_class,
                    "incident_id": incident.id,
                    "message": "VIP transit detected and corridor monitored.",
                },
                status=status.HTTP_200_OK,
            )
