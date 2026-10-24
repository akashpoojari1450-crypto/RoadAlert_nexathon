import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from alerts.models import (
    AlertCooldown,
    BlockageSensorEvent,
    EmergencyCallRecord,
    EmergencyRecipient,
    IoTDevice,
    validate_e164_phone,
)
from alerts.services.dispatcher import AlertDispatcher
from alerts.services.esp32_service import ESP32BlockageService
from alerts.services.twilio_service import TwilioVoiceService
from incidents.models import Incident, IncidentType, Severity, VerificationLevel


class EmergencyVoiceAlertTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Users with various roles
        self.authority_user = User.objects.create_user(
            username="auth_officer",
            password="password123",
            role=User.Role.AUTHORITY,
            phone="+919876543210",
        )
        self.passenger_user = User.objects.create_user(
            username="regular_user",
            password="password123",
            role=User.Role.PASSENGER,
            phone="+919876543211",
        )

        # Verified emergency recipients
        self.recipient_1 = EmergencyRecipient.objects.create(
            name="Control Room Dispatch",
            phone_number="+919876543210",
            role_category=EmergencyRecipient.RoleCategory.AUTHORITY,
            region="all",
            is_active=True,
            is_verified=True,
            consent_received=True,
            opt_out=False,
        )
        self.recipient_2 = EmergencyRecipient.objects.create(
            name="Ambulance Emergency Service",
            phone_number="+919876543222",
            role_category=EmergencyRecipient.RoleCategory.EMERGENCY_SERVICES,
            region="NH-66",
            is_active=True,
            is_verified=True,
            consent_received=True,
            opt_out=False,
        )
        self.opted_out_recipient = EmergencyRecipient.objects.create(
            name="Opted Out Contact",
            phone_number="+919876543299",
            role_category=EmergencyRecipient.RoleCategory.GENERAL,
            is_active=True,
            is_verified=True,
            consent_received=True,
            opt_out=True,
        )

        # Active test incident
        self.incident = Incident.objects.create(
            incident_type=IncidentType.ROAD_CLOSURE,
            road_name="NH-66 - Surathkal Bridge",
            latitude=12.9850,
            longitude=74.8020,
            severity=Severity.SEVERE,
            verification_level=VerificationLevel.AUTHORITY_CONFIRMED,
            report_count=3,
            confidence=95,
            estimated_delay_min=30,
            started_at=timezone.now(),
        )

        # IoT ESP32 Device
        self.device = IoTDevice.objects.create(
            device_id="ESP32-NH66-01",
            name="Surathkal North Sensor",
            api_key="secret-iot-token-987",
            road_name="NH-66 - Surathkal Bridge",
            latitude=12.9850,
            longitude=74.8020,
            distance_threshold_cm=120.0,
            consecutive_required=3,
            is_active=True,
        )

    # --------------------------------------------------------------------------
    # 1. Phone number validation
    # --------------------------------------------------------------------------
    def test_e164_phone_validation(self):
        # Valid numbers
        validate_e164_phone("+919876543210")
        validate_e164_phone("+14155552671")

        # Invalid numbers should raise ValidationError
        with self.assertRaises(Exception):
            validate_e164_phone("9876543210")
        with self.assertRaises(Exception):
            validate_e164_phone("invalid_phone")
        with self.assertRaises(Exception):
            validate_e164_phone("+1")

    # --------------------------------------------------------------------------
    # 2. Permissions: Manual Trigger Call
    # --------------------------------------------------------------------------
    def test_trigger_call_unauthorized_for_anonymous_or_passenger(self):
        # Anonymous
        res = self.client.post("/api/alerts/trigger-call/", {"incident_id": self.incident.id})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

        # Passenger
        self.client.force_authenticate(user=self.passenger_user)
        res = self.client.post("/api/alerts/trigger-call/", {"incident_id": self.incident.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_trigger_call_authorized_for_authority(self):
        self.client.force_authenticate(user=self.authority_user)
        res = self.client.post(
            "/api/alerts/trigger-call/",
            {
                "incident_id": self.incident.id,
                "recipient_group": "all",
                "custom_instructions": "Take alternative bypass road.",
                "alternative_route": "Bajpe Road",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["queued_calls"], 1)

        # Verify call record created in database with masked phone
        records = EmergencyCallRecord.objects.filter(incident=self.incident)
        self.assertGreaterEqual(records.count(), 1)
        first_call = records.first()
        self.assertIn("****", first_call.phone_number_masked)
        self.assertIn("RoadAlert Emergency Notification", first_call.twiml_message)

    # --------------------------------------------------------------------------
    # 3. Cooldown & Rate Limiting Prevention
    # --------------------------------------------------------------------------
    def test_duplicate_dispatch_cooldown_prevention(self):
        self.client.force_authenticate(user=self.authority_user)

        # First dispatch succeeds
        res1 = self.client.post(
            "/api/alerts/trigger-call/",
            {"incident_id": self.incident.id, "recipient_group": "all"},
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_202_ACCEPTED)

        # Immediate second dispatch is blocked by active cooldown
        res2 = self.client.post(
            "/api/alerts/trigger-call/",
            {"incident_id": self.incident.id, "recipient_group": "all"},
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("COOLDOWN_ACTIVE", res2.json().get("error", ""))

    # --------------------------------------------------------------------------
    # 4. Dry-Run Mode vs Live Twilio Dispatching
    # --------------------------------------------------------------------------
    def test_dry_run_mode_simulation(self):
        service = TwilioVoiceService()
        service.calls_enabled = False  # Dry-run

        result = service.trigger_voice_call(
            to_phone="+919876543210",
            speech_text="Emergency test message",
            record_id=101,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["is_dry_run"])
        self.assertTrue(result["call_sid"].startswith("SIM_"))

    @patch("alerts.services.twilio_service.Client")
    def test_live_twilio_dispatch_mocked(self, mock_twilio_client):
        # Configure mock Twilio client
        mock_instance = MagicMock()
        mock_call = MagicMock()
        mock_call.sid = "CA1234567890abcdef1234567890abcdef"
        mock_call.status = "queued"
        mock_instance.calls.create.return_value = mock_call
        mock_twilio_client.return_value = mock_instance

        service = TwilioVoiceService()
        service.calls_enabled = True
        service.account_sid = "AC_TEST_ACCOUNT_SID"
        service.auth_token = "TEST_AUTH_TOKEN"
        service.from_number = "+15005550006"

        result = service.trigger_voice_call(
            to_phone="+919876543210",
            speech_text="Emergency test message",
            record_id=102,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["call_sid"], "CA1234567890abcdef1234567890abcdef")
        mock_instance.calls.create.assert_called_once()

    # --------------------------------------------------------------------------
    # 5. Twilio TwiML Generation & Webhook Endpoint
    # --------------------------------------------------------------------------
    def test_twiml_generation_and_view(self):
        record = EmergencyCallRecord.objects.create(
            incident=self.incident,
            recipient=self.recipient_1,
            phone_number_masked=self.recipient_1.masked_phone,
            destination_phone_raw=self.recipient_1.phone_number,
            twiml_message="Emergency road blockage on NH-66.",
        )

        res = self.client.get(f"/api/alerts/twilio/twiml/{record.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/xml")
        xml_content = res.content.decode("utf-8")
        self.assertIn("<Response>", xml_content)
        self.assertIn("<Say", xml_content)
        self.assertIn("Emergency road blockage on NH-66.", xml_content)

    # --------------------------------------------------------------------------
    # 6. Twilio Status Callback & State Transition
    # --------------------------------------------------------------------------
    def test_twilio_status_callback_updates_record(self):
        record = EmergencyCallRecord.objects.create(
            incident=self.incident,
            recipient=self.recipient_1,
            phone_number_masked=self.recipient_1.masked_phone,
            call_sid="CA_CALLBACK_TEST_123",
            status=EmergencyCallRecord.CallStatus.INITIATED,
        )

        # Simulate Twilio webhook callback with signature bypass for test
        with patch.object(TwilioVoiceService, "validate_twilio_signature", return_value=True):
            res = self.client.post(
                "/api/alerts/twilio/status-callback/",
                {
                    "CallSid": "CA_CALLBACK_TEST_123",
                    "CallStatus": "completed",
                    "CallDuration": "42",
                },
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        record.refresh_from_db()
        self.assertEqual(record.status, EmergencyCallRecord.CallStatus.COMPLETED)
        self.assertEqual(record.duration_seconds, 42)
        self.assertIsNotNone(record.completed_at)

    # --------------------------------------------------------------------------
    # 7. ESP32 Sensor Intake & Multi-sample Verification
    # --------------------------------------------------------------------------
    def test_esp32_device_authentication_rejection(self):
        # Missing token
        res = self.client.post(
            "/api/alerts/road-blockage/",
            {"distance_cm": 45.0, "duration_seconds": 10},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

        # Wrong token
        res2 = self.client.post(
            "/api/alerts/road-blockage/",
            {"distance_cm": 45.0, "duration_seconds": 10},
            HTTP_X_DEVICE_TOKEN="wrong_token",
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_esp32_consecutive_reading_safeguard(self):
        # 1st reading: below threshold (obstruction), but not yet verified (threshold=3)
        res1 = self.client.post(
            "/api/alerts/road-blockage/",
            {"distance_cm": 50.0, "duration_seconds": 5},
            HTTP_X_DEVICE_TOKEN="secret-iot-token-987",
            format="json",
        )
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertFalse(res1.json()["verified_blockage"])

        # 2nd reading: still below threshold, count=2
        res2 = self.client.post(
            "/api/alerts/road-blockage/",
            {"distance_cm": 48.0, "duration_seconds": 10},
            HTTP_X_DEVICE_TOKEN="secret-iot-token-987",
            format="json",
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertFalse(res2.json()["verified_blockage"])

        # 3rd reading: count=3 -> Trigger verified blockage and queue calls!
        res3 = self.client.post(
            "/api/alerts/road-blockage/",
            {"distance_cm": 46.0, "duration_seconds": 15},
            HTTP_X_DEVICE_TOKEN="secret-iot-token-987",
            format="json",
        )
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        data3 = res3.json()
        self.assertTrue(data3["verified_blockage"])
        self.assertIn("incident_id", data3)

        # Confirm an automated call record was created with source ESP32
        esp32_calls = EmergencyCallRecord.objects.filter(
            trigger_source=EmergencyCallRecord.TriggerSource.ESP32
        )
        self.assertGreaterEqual(esp32_calls.count(), 1)

    # --------------------------------------------------------------------------
    # 8. Recipient Selection & Opt-out Rules
    # --------------------------------------------------------------------------
    def test_opted_out_recipients_are_never_called(self):
        recipients = AlertDispatcher.dispatch_for_incident(
            incident=self.incident,
            trigger_source=EmergencyCallRecord.TriggerSource.MANUAL,
        )
        self.assertTrue(recipients["success"])

        # Ensure opted out phone number was not called
        called_numbers = EmergencyCallRecord.objects.filter(
            incident=self.incident
        ).values_list("destination_phone_raw", flat=True)
        self.assertNotIn(self.opted_out_recipient.phone_number, called_numbers)

    # --------------------------------------------------------------------------
    # 9. Speech Preview Endpoint
    # --------------------------------------------------------------------------
    def test_speech_preview_endpoint(self):
        self.client.force_authenticate(user=self.authority_user)
        res = self.client.post(
            "/api/alerts/preview-message/",
            {
                "incident_id": self.incident.id,
                "alternative_route": "Mangaluru Bypass",
                "custom_instructions": "Follow speed limits",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        preview = res.json()["preview_text"]
        self.assertIn("RoadAlert Emergency Notification", preview)
        self.assertIn("Surathkal Bridge", preview)
        self.assertIn("Mangaluru Bypass", preview)
