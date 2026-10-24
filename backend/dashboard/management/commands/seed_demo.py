"""
python manage.py seed_demo [--reset]

Creates a demo company, accounts, journeys and incidents so the dashboards have
something to show. DEMO ONLY: the accounts use a well-known password, so the
command refuses to run unless DEBUG is on.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Company, TrackingPolicy, User
from incidents.models import Incident
from incidents.verification import submit_report
from journeys.models import GpsPoint, Journey

PASSWORD = "demo12345"
COMPANY = "Acme Logistics (demo)"
USERNAMES = ["demo_employer", "demo_authority", "demo_alice", "demo_bob", "demo_carol", "demo_dev", "demo_passenger"]

THOKKOTU = (12.8160, 74.8500)
PUMPWELL = (12.8670, 74.8630)
KOTTARA = (12.9020, 74.8350)
MULKI = (13.0900, 74.7900)


def trail(journey, point, minutes, speed):
    now = timezone.now()
    GpsPoint.objects.bulk_create([
        GpsPoint(journey=journey, latitude=point[0], longitude=point[1], speed_kmh=speed,
                 recorded_at=now - timedelta(minutes=minutes - i))
        for i in range(minutes + 1)
    ])


class Command(BaseCommand):
    help = "Create demo data for the dashboards (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete earlier demo data first.")
        parser.add_argument("--accounts-only", action="store_true",
                            help="Create only the company and accounts (no journeys or incidents). "
                                 "Use before simulate_live for a clean dashboard.")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo creates accounts with a known password; it only runs when DEBUG is True.")

        exists = User.objects.filter(username__in=USERNAMES).exists()
        if exists and not opts["reset"]:
            raise CommandError("Demo data already exists. Run with --reset to rebuild it.")
        if opts["reset"]:
            User.objects.filter(username__in=USERNAMES).delete()
            Company.objects.filter(name=COMPANY).delete()
            Incident.objects.filter(road_name__endswith=" (demo)").delete()

        company = Company.objects.create(name=COMPANY)
        TrackingPolicy.objects.create(company=company, scope=TrackingPolicy.Scope.JOURNEY_STARTED)

        def make(name, role, **kw):
            return User.objects.create_user(name, password=PASSWORD, role=role, **kw)

        make("demo_employer", User.Role.EMPLOYER, company=company)
        make("demo_authority", User.Role.AUTHORITY)
        make("demo_passenger", User.Role.PASSENGER)
        now = timezone.now()
        alice = make("demo_alice", User.Role.EMPLOYEE, company=company, tracking_consent=True, consent_given_at=now)
        bob = make("demo_bob", User.Role.EMPLOYEE, company=company, tracking_consent=True, consent_given_at=now)
        carol = make("demo_carol", User.Role.EMPLOYEE, company=company, tracking_consent=True, consent_given_at=now)
        dev = make("demo_dev", User.Role.EMPLOYEE, company=company)  # has NOT opted in

        if opts["accounts_only"]:
            self.stdout.write(self.style.SUCCESS("Demo accounts created (no journeys or incidents)."))
            self._print_logins()
            return

        def journey(user, origin, dest, o_name, d_name, shared=True):
            return Journey.objects.create(
                user=user, kind="commute", origin_name=o_name, destination_name=d_name,
                origin_lat=origin[0], origin_lng=origin[1], dest_lat=dest[0], dest_lng=dest[1],
                shared_with_employer=shared,
            )

        # Alice: crawls at Thokkotu for 35 minutes -> "Traffic" with GPS-speed evidence.
        ja = journey(alice, THOKKOTU, MULKI, "Mangaluru", "Udupi")
        trail(ja, THOKKOTU, 35, 2)
        submit_report(reporter=alice, incident_type="heavy_traffic", lat=THOKKOTU[0], lng=THOKKOTU[1],
                      road_name="NH-66 - Thokkotu (demo)")

        # Bob: stuck behind a road closure at Pumpwell -> "Road Block".
        jb = journey(bob, PUMPWELL, KOTTARA, "Pumpwell", "Kottara")
        trail(jb, PUMPWELL, 12, 1)
        submit_report(reporter=bob, incident_type="road_closure", lat=PUMPWELL[0], lng=PUMPWELL[1],
                      road_name="NH-66 - Pumpwell (demo)")

        # Carol: moving normally -> "Normal".
        jc = journey(carol, KOTTARA, MULKI, "Kottara", "Mulki")
        trail(jc, KOTTARA, 10, 48)

        # Dev has not opted in: his journey exists but must never reach the employer page.
        jd = journey(dev, KOTTARA, THOKKOTU, "Kottara", "Thokkotu", shared=False)
        trail(jd, KOTTARA, 10, 3)

        # History for the monthly report (this month and last).
        history = [
            ("accident", "NH-66 - Pumpwell (demo)", PUMPWELL, 55, 2),
            ("accident", "NH-66 - Pumpwell (demo)", PUMPWELL, 40, 5),
            ("road_repair", "NH-66 - Pumpwell (demo)", PUMPWELL, 25, 9),
            ("flood", "Kottara Chowki (demo)", KOTTARA, 90, 3),
            ("flood", "Kottara Chowki (demo)", KOTTARA, 70, 12),
            ("heavy_rain", "Kottara Chowki (demo)", KOTTARA, 35, 12),
            ("festival", "Hampankatta (demo)", (12.8680, 74.8420), 45, 4),
            ("signal_failure", "Thokkotu Junction (demo)", THOKKOTU, 30, 7),
            ("breakdown", "Mulki Bridge (demo)", MULKI, 20, 40),  # last month
            ("accident", "Mulki Bridge (demo)", MULKI, 65, 45),   # last month
        ]
        for kind, road, (lat, lng), delay, days_ago in history:
            when = now - timedelta(days=days_ago)
            Incident.objects.create(
                incident_type=kind, road_name=road, latitude=lat, longitude=lng, severity="heavy",
                verification_level=3, confidence=80, report_count=3, estimated_delay_min=delay,
                started_at=when, resolved_at=when + timedelta(minutes=delay + 20),
            )

        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self._print_logins()

    def _print_logins(self):
        self.stdout.write("Sign in at http://127.0.0.1:8000/ with:")
        self.stdout.write(f"  Employer  demo_employer   / {PASSWORD}")
        self.stdout.write(f"  Authority demo_authority  / {PASSWORD}")
        self.stdout.write("Employees demo_alice, demo_bob, demo_carol, demo_dev use the same password (for the app).")
