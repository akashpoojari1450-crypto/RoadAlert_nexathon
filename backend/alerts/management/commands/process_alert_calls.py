import logging
import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from alerts.models import EmergencyCallRecord
from alerts.services.dispatcher import _execute_single_call

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Processes pending queued and retryable emergency voice alert calls."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process pending calls once and exit.")
        parser.add_argument("--poll-interval", type=int, default=5, help="Seconds to wait between polls in daemon mode.")

    def handle(self, *args, **options):
        once = options["once"]
        poll_interval = options["poll_interval"]
        self.stdout.write(self.style.SUCCESS("Starting RoadAlert Emergency Call Worker..."))

        while True:
            # Fetch queued calls
            pending = EmergencyCallRecord.objects.filter(
                status=EmergencyCallRecord.CallStatus.QUEUED
            ).order_by("created_at")[:10]

            count = len(pending)
            if count > 0:
                self.stdout.write(f"Processing {count} queued call(s)...")
                for record in pending:
                    _execute_single_call(record.id)

            if once:
                break
            time.sleep(poll_interval)
