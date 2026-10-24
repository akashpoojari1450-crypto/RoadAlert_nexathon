from django.core.management.base import BaseCommand
from alerts.models import EmergencyRecipient, IoTDevice


class Command(BaseCommand):
    help = "Seeds initial emergency recipients and an IoT device for demoing voice alerts."

    def handle(self, *args, **options):
        # 1. Seed Emergency Recipients
        recipients_data = [
            {
                "name": "Traffic Police Control Room",
                "phone_number": "+919876543210",
                "role_category": EmergencyRecipient.RoleCategory.AUTHORITY,
                "region": "all",
                "severity_threshold": "moderate",
            },
            {
                "name": "Emergency Medical Dispatch (Ambulance)",
                "phone_number": "+919876543222",
                "role_category": EmergencyRecipient.RoleCategory.EMERGENCY_SERVICES,
                "region": "NH-66",
                "severity_threshold": "severe",
            },
            {
                "name": "District Logistics Fleet Manager",
                "phone_number": "+919876543233",
                "role_category": EmergencyRecipient.RoleCategory.TRANSPORT_MANAGER,
                "region": "Mangaluru",
                "severity_threshold": "heavy",
            },
        ]

        created_r = 0
        for r_info in recipients_data:
            _, created = EmergencyRecipient.objects.get_or_create(
                phone_number=r_info["phone_number"],
                defaults={
                    "name": r_info["name"],
                    "role_category": r_info["role_category"],
                    "region": r_info["region"],
                    "severity_threshold": r_info["severity_threshold"],
                    "is_active": True,
                    "is_verified": True,
                    "consent_received": True,
                    "opt_out": False,
                },
            )
            if created:
                created_r += 1

        # 2. Seed ESP32 Node
        device, dev_created = IoTDevice.objects.get_or_create(
            device_id="ESP32-NH66-01",
            defaults={
                "name": "Surathkal Bridge Roadside Node",
                "api_key": "esp32_demo_key_12345",
                "road_name": "NH-66 - Surathkal Bridge",
                "latitude": 12.9850,
                "longitude": 74.8020,
                "distance_threshold_cm": 120.0,
                "consecutive_required": 3,
                "is_active": True,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded: {created_r} recipient(s), ESP32 Device '{device.device_id}' (Key: {device.api_key})"
            )
        )
