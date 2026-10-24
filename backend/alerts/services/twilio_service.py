import logging
import os
import re
import uuid
from datetime import datetime
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Optional twilio imports with graceful handling
try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client
    from twilio.request_validator import RequestValidator
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    TwilioRestException = Exception
    Client = None
    RequestValidator = None


class TwilioVoiceService:
    """
    Production-ready Twilio Programmable Voice service for RoadAlert.
    Supports dry-run testing, safe credential handling, TwiML generation,
    signature validation, and error classification.
    """

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", getattr(settings, "TWILIO_ACCOUNT_SID", "")).strip()
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", getattr(settings, "TWILIO_AUTH_TOKEN", "")).strip()
        self.from_number = os.getenv("TWILIO_PHONE_NUMBER", getattr(settings, "TWILIO_PHONE_NUMBER", "")).strip()
        self.public_base_url = os.getenv(
            "PUBLIC_BASE_URL", getattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        
        # Kill switch: Default to False for safety until explicitly configured
        calls_enabled_raw = os.getenv("ROADALERT_CALLS_ENABLED", getattr(settings, "ROADALERT_CALLS_ENABLED", "false"))
        self.calls_enabled = str(calls_enabled_raw).lower() in ("true", "1", "yes")

        # Allowlist for test environments (comma separated E.164 numbers)
        allowlist_raw = os.getenv("ROADALERT_CALL_ALLOWLIST", getattr(settings, "ROADALERT_CALL_ALLOWLIST", ""))
        self.allowlist = [n.strip() for n in allowlist_raw.split(",") if n.strip()]

        self.voice_name = getattr(settings, "TWILIO_VOICE_NAME", "alice")
        self.voice_language = getattr(settings, "TWILIO_VOICE_LANGUAGE", "en-IN")

    def is_configured(self) -> bool:
        """Checks if Twilio credentials and from_number are present."""
        return bool(self.account_sid and self.auth_token and self.from_number)

    def is_number_allowed(self, destination_number: str) -> bool:
        """
        If an allowlist is configured, checks whether destination is permitted.
        """
        if not self.allowlist:
            return True
        clean = destination_number.replace(" ", "").replace("-", "")
        return clean in self.allowlist

    def build_emergency_speech_text(
        self,
        incident_type: str = "road blockage",
        road_name: str = "",
        severity: str = "severe",
        detection_time: datetime = None,
        alternative_route: str = "",
        instructions: str = "",
    ) -> str:
        """
        Creates a clear, professional, emergency announcement.
        Uses verified dynamic facts without fabricating details.
        """
        location_phrase = f"on {road_name}" if road_name else "in your immediate area"
        type_clean = incident_type.replace("_", " ").title()
        severity_clean = severity.title()

        if detection_time:
            time_str = detection_time.strftime("%I:%M %p")
            time_phrase = f"detected at {time_str}"
        else:
            time_phrase = "recently detected"

        speech = (
            f"RoadAlert Emergency Notification. Attention. "
            f"A {severity_clean} {type_clean} has been {location_phrase}, {time_phrase}. "
            f"Please remain alert and avoid this corridor. "
        )

        if alternative_route:
            speech += f"Recommended alternate route: {alternative_route}. "

        if instructions:
            speech += f"Authority advisory: {instructions}. "
        else:
            speech += (
                "Drivers are advised to slow down and follow on-site traffic signs. "
            )

        speech += (
            "Check the official RoadAlert dashboard or mobile application for live updates. "
            "This was an automated safety dispatch."
        )
        return speech

    def generate_twiml(self, speech_text: str) -> str:
        """
        Constructs safe TwiML XML with <Say> and <Pause> for natural speech.
        """
        # Clean text for XML safe insertion
        safe_text = (
            speech_text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Response>\n'
            f'    <Pause length="1"/>\n'
            f'    <Say voice="{self.voice_name}" language="{self.voice_language}">\n'
            f'        {safe_text}\n'
            f'    </Say>\n'
            f'    <Pause length="2"/>\n'
            f'    <Say voice="{self.voice_name}" language="{self.voice_language}">\n'
            f'        Repeating emergency alert. {safe_text}\n'
            f'    </Say>\n'
            f'    <Pause length="1"/>\n'
            f'</Response>'
        )

    def trigger_voice_call(
        self,
        to_phone: str,
        speech_text: str,
        record_id: int,
        idempotency_key: str = None,
    ) -> dict:
        """
        Initiates an outbound emergency call via Twilio.
        If calls are disabled (dry-run mode), simulates the dispatch safely.
        """
        clean_to = to_phone.strip().replace(" ", "").replace("-", "")

        # Check allowlist in dev
        if not self.is_number_allowed(clean_to):
            logger.warning("Number %s blocked by ROADALERT_CALL_ALLOWLIST", clean_to[:5] + "****")
            return {
                "success": False,
                "call_sid": "",
                "status": "failed",
                "error_category": "ALLOWLIST_RESTRICTION",
                "error_message": "Destination phone number is not in the development allowlist.",
            }

        # Dry-run or disabled mode
        if not self.calls_enabled:
            simulated_sid = f"SIM_{uuid.uuid4().hex[:20]}"
            logger.info(
                "[DRY-RUN] Outbound call simulated to %s | SID: %s | Message length: %d",
                clean_to[:5] + "****",
                simulated_sid,
                len(speech_text),
            )
            return {
                "success": True,
                "call_sid": simulated_sid,
                "status": "queued",
                "is_dry_run": True,
                "message": "Call simulated successfully (ROADALERT_CALLS_ENABLED is False).",
            }

        # Check real Twilio configuration
        if not self.is_configured():
            logger.error("Twilio credentials not configured in environment.")
            return {
                "success": False,
                "call_sid": "",
                "status": "failed",
                "error_category": "CREDENTIALS_MISSING",
                "error_message": "Twilio Account SID, Auth Token, or Phone Number is missing.",
            }

        if not TWILIO_AVAILABLE:
            return {
                "success": False,
                "call_sid": "",
                "status": "failed",
                "error_category": "SDK_MISSING",
                "error_message": "Twilio Python library not installed.",
            }

        status_callback_url = f"{self.public_base_url}/api/alerts/twilio/status-callback/"
        twiml_url = f"{self.public_base_url}/api/alerts/twilio/twiml/{record_id}/"

        try:
            client = Client(self.account_sid, self.auth_token)
            call = client.calls.create(
                to=clean_to,
                from_=self.from_number,
                url=twiml_url,
                method="POST",
                status_callback=status_callback_url,
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                status_callback_method="POST",
                timeout=30,  # Ring up to 30 seconds
            )
            return {
                "success": True,
                "call_sid": call.sid,
                "status": getattr(call, "status", "queued"),
                "is_dry_run": False,
            }
        except TwilioRestException as exc:
            logger.error("Twilio API error (%s): %s", exc.code, exc.msg)
            # Classify error code
            error_cat = "TWILIO_ERROR"
            if exc.code == 21211:
                error_cat = "INVALID_NUMBER"
            elif exc.code == 21216:
                error_cat = "UNVERIFIED_NUMBER_TRIAL"
            elif exc.code == 20003:
                error_cat = "AUTH_FAILED"
            elif exc.code == 20429:
                error_cat = "RATE_LIMIT_EXCEEDED"
            elif exc.code == 21614:
                error_cat = "CANNOT_ROUTE_CALL"
            elif exc.code == 20008:
                error_cat = "INSUFFICIENT_FUNDS"

            return {
                "success": False,
                "call_sid": "",
                "status": "failed",
                "error_category": error_cat,
                "error_message": str(exc.msg or exc),
                "error_code": exc.code,
            }
        except Exception as exc:
            logger.exception("Unexpected exception initiating Twilio voice call: %s", exc)
            return {
                "success": False,
                "call_sid": "",
                "status": "failed",
                "error_category": "NETWORK_OR_SERVER_ERROR",
                "error_message": str(exc),
            }

    def validate_twilio_signature(self, request_url: str, post_data: dict, signature: str) -> bool:
        """
        Validates incoming webhook request using Twilio RequestValidator.
        In dry-run or local debug without auth token, logs a warning.
        """
        if not self.auth_token or not TWILIO_AVAILABLE or not RequestValidator:
            # If not configured or running in test mode
            return True

        if not signature:
            logger.warning("Twilio webhook signature missing.")
            return False

        validator = RequestValidator(self.auth_token)
        # Ensure post_data dictionary values are strings
        cleaned_params = {k: str(v) for k, v in post_data.items()}
        valid = validator.validate(request_url, cleaned_params, signature)
        if not valid:
            logger.warning("Twilio webhook signature validation failed for URL: %s", request_url)
        return valid
