from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Company, TrackingPolicy, User
from incidents.models import Incident
from journeys.models import GpsPoint, Journey

LAT, LNG = 12.8160, 74.8500


def make(name, role, **kw):
    return User.objects.create_user(name, password="pw12345678", role=role, **kw)


class AccessTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Acme")
        self.employer = make("boss", User.Role.EMPLOYER, company=self.co)
        self.authority = make("auth", User.Role.AUTHORITY)
        self.employee = make("emp", User.Role.EMPLOYEE, company=self.co)
        self.passenger = make("pax", User.Role.PASSENGER)
        self.client = Client()

    def test_anonymous_redirected_to_login(self):
        for url in ("/", "/employer/", "/authority/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertTrue(r["Location"].startswith("/login/"), url)

    def test_login_page_renders(self):
        self.assertContains(self.client.get("/login/"), "Sign in")

    def test_login_and_role_redirect(self):
        for user, target in ((self.employer, "/employer/"), (self.authority, "/authority/")):
            c = Client()
            r = c.post("/login/", {"username": user.username, "password": "pw12345678"})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(c.get("/").status_code, 302)
            self.assertEqual(c.get("/")["Location"], target)

    def test_wrong_password_shows_error(self):
        r = self.client.post("/login/", {"username": "boss", "password": "nope"})
        self.assertContains(r, "don't match")

    def test_passenger_and_employee_get_no_dashboard(self):
        for u in (self.passenger, self.employee):
            self.client.force_login(u)
            self.assertContains(self.client.get("/"), "No dashboard for your account")
            self.assertEqual(self.client.get("/employer/").status_code, 403)
            self.assertEqual(self.client.get("/authority/").status_code, 403)

    def test_employer_cannot_open_authority_page_and_vice_versa(self):
        self.client.force_login(self.employer)
        self.assertEqual(self.client.get("/authority/").status_code, 403)
        self.client.force_login(self.authority)
        self.assertEqual(self.client.get("/employer/").status_code, 403)

    def test_employer_without_company_has_no_page(self):
        orphan = make("orphan", User.Role.EMPLOYER)
        self.client.force_login(orphan)
        self.assertEqual(self.client.get("/employer/").status_code, 403)

    def test_logout_needs_post(self):
        self.client.force_login(self.employer)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertEqual(self.client.post("/logout/").status_code, 302)
        self.assertEqual(self.client.get("/employer/").status_code, 302)


class EmployerPageTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Acme")
        TrackingPolicy.objects.create(company=self.co)
        self.boss = make("boss", User.Role.EMPLOYER, company=self.co)
        self.client = Client()
        self.client.force_login(self.boss)

    def _trip(self, name, speed, consent=True, shared=True, company=None):
        u = make(name, User.Role.EMPLOYEE, company=company or self.co, tracking_consent=consent)
        j = Journey.objects.create(user=u, origin_name="X", destination_name="Y", origin_lat=LAT, origin_lng=LNG,
                                   dest_lat=LAT + 1, dest_lng=LNG, shared_with_employer=shared)
        now = timezone.now()
        GpsPoint.objects.bulk_create([GpsPoint(journey=j, latitude=LAT, longitude=LNG, speed_kmh=speed,
                                               recorded_at=now - timedelta(minutes=10 - i)) for i in range(11)])
        return u

    def test_empty_state(self):
        self.assertContains(self.client.get("/employer/"), "No shared journeys")

    def test_shows_status_delay_and_evidence(self):
        self._trip("alice", speed=2)
        self._trip("carol", speed=50)
        html = self.client.get("/employer/").content.decode()
        self.assertIn("alice", html)
        self.assertIn("10 min", html)
        self.assertIn("remained in the affected zone", html)
        self.assertIn("carol", html)
        self.assertIn("Normal", html)

    def test_hides_unconsented_and_other_companies(self):
        self._trip("zz_optout", speed=2, consent=False, shared=False)
        other = Company.objects.create(name="Other")
        self._trip("outsider", speed=2, company=other)
        html = self.client.get("/employer/").content.decode()
        self.assertNotIn("zz_optout", html)
        self.assertNotIn("outsider", html)

    def test_sharing_counter(self):
        self._trip("a", 2)
        self._trip("b", 2, consent=False, shared=False)
        self.assertContains(self.client.get("/employer/"), "1 / 2")

    def test_bad_hours_value_does_not_crash(self):
        for bad in ("abc", "-5", "99999", ""):
            self.assertEqual(self.client.get("/employer/", {"hours": bad}).status_code, 200, bad)


class AuthorityPageTests(TestCase):
    def setUp(self):
        self.auth = make("auth", User.Role.AUTHORITY)
        self.client = Client()
        self.client.force_login(self.auth)
        self.inc = Incident.objects.create(
            incident_type="accident", road_name="NH-66 - Pumpwell", latitude=LAT, longitude=LNG,
            started_at=timezone.now(), estimated_delay_min=60, verification_level=3, report_count=3)

    def test_lists_active_incident_and_report(self):
        html = self.client.get("/authority/").content.decode()
        self.assertIn("NH-66 - Pumpwell", html)
        self.assertIn("Level 3", html)
        self.assertIn("Monthly report", html)

    def test_confirm_sets_level_6(self):
        r = self.client.post(f"/authority/incidents/{self.inc.id}/confirm/")
        self.assertEqual(r.status_code, 302)
        self.inc.refresh_from_db()
        self.assertEqual((self.inc.authority_confirmed, self.inc.verification_level, self.inc.confidence), (True, 6, 100))

    def test_resolve_removes_from_live_list(self):
        self.client.post(f"/authority/incidents/{self.inc.id}/resolve/")
        self.assertContains(self.client.get("/authority/"), "No active incidents")

    def test_actions_need_post_and_authority_role_and_csrf(self):
        self.assertEqual(self.client.get(f"/authority/incidents/{self.inc.id}/confirm/").status_code, 405)
        pax = Client()
        pax.force_login(make("pax", User.Role.PASSENGER))
        self.assertEqual(pax.post(f"/authority/incidents/{self.inc.id}/confirm/").status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.auth)
        self.assertEqual(strict.post(f"/authority/incidents/{self.inc.id}/confirm/").status_code, 403)
        self.inc.refresh_from_db()
        self.assertFalse(self.inc.authority_confirmed)

    def test_unknown_action_and_incident(self):
        self.assertEqual(self.client.post(f"/authority/incidents/{self.inc.id}/delete/").status_code, 404)
        self.assertEqual(self.client.post("/authority/incidents/99999/confirm/").status_code, 404)

    def test_bad_month_and_year_fall_back(self):
        for params in ({"month": "13"}, {"month": "x"}, {"year": "0"}, {"year": "99999"}):
            self.assertEqual(self.client.get("/authority/", params).status_code, 200, params)

    def test_empty_month_message(self):
        self.assertContains(self.client.get("/authority/", {"year": 2020, "month": 1}), "No incidents recorded for 2020-01")


class SeedDemoTests(TestCase):
    def run_seed(self, *args):
        out = StringIO()
        call_command("seed_demo", *args, stdout=out)
        return out.getvalue()

    @override_settings(DEBUG=False)
    def test_refuses_when_debug_off(self):
        with self.assertRaisesMessage(CommandError, "only runs when DEBUG is True"):
            self.run_seed()
        self.assertFalse(User.objects.filter(username="demo_employer").exists())

    @override_settings(DEBUG=True)
    def test_creates_working_demo_and_pages_show_it(self):
        self.run_seed()
        c = Client()
        self.assertTrue(c.login(username="demo_employer", password="demo12345"))
        html = c.get("/employer/").content.decode()
        self.assertIn("demo_alice", html)
        self.assertIn("Traffic", html)
        self.assertIn("Road Block", html)
        self.assertNotIn("demo_dev", html)          # opted out: never shown
        self.assertEqual(html.count("Multiple users"), 0)  # the old-incident bug
        c2 = Client()
        c2.login(username="demo_authority", password="demo12345")
        self.assertContains(c2.get("/authority/"), "Pumpwell")

    @override_settings(DEBUG=True)
    def test_second_run_needs_reset_and_reset_works(self):
        self.run_seed()
        with self.assertRaisesMessage(CommandError, "--reset"):
            self.run_seed()
        self.run_seed("--reset")
        self.assertEqual(User.objects.filter(username="demo_alice").count(), 1)
        self.assertEqual(Company.objects.filter(name__contains="demo").count(), 1)


from django.test import LiveServerTestCase  # noqa: E402

from dashboard import simulator  # noqa: E402
from notifications.models import Notification  # noqa: E402


@override_settings(DEBUG=True)
class LiveSimulationTests(LiveServerTestCase):
    """Runs the whole phone-to-dashboard scenario over real HTTP against a live test server."""

    def setUp(self):
        call_command("seed_demo", "--accounts-only", stdout=StringIO())

    def test_full_scenario(self):
        s = simulator.run(self.live_server_url, interval=0, log=lambda m: None)

        # three reports from three people merged into ONE incident, then confirmed and resolved
        self.assertEqual(len(s["incident_ids"]), 1)
        inc = Incident.objects.get(pk=s["incident"]["id"])
        self.assertEqual(inc.reports.count(), 3)
        self.assertEqual(inc.verification_level, 6)
        self.assertEqual(inc.confidence, 100)
        self.assertIsNotNone(inc.resolved_at)
        self.assertGreaterEqual(inc.estimated_delay_min, 10)

        # the driver still approaching was warned exactly once; those already in the jam were not
        self.assertEqual(Notification.objects.filter(user__username="demo_carol", kind="traffic_alert").count(), 1)
        for name in ("demo_alice", "demo_bob", "demo_passenger"):
            self.assertFalse(Notification.objects.filter(user__username=name).exists(), name)

        # employer got a count (only the one driver still ahead), never names
        notes = Notification.objects.filter(user__username="demo_employer")
        self.assertEqual(notes.count(), 1)
        self.assertIn("1 employee ", notes[0].body)
        self.assertNotIn("demo_", notes[0].body)

        # employer dashboard: shared employees only, the passenger's private journey never appears
        names = {r["employee"] for r in s["dashboard"]}
        self.assertEqual(names, {"demo_alice", "demo_bob", "demo_carol"})
        stuck = {r["employee"]: r["delay_min"] for r in s["dashboard"]}
        self.assertGreaterEqual(stuck["demo_alice"], 20)
        self.assertGreaterEqual(stuck["demo_bob"], 20)

    def test_clear_error_when_accounts_missing(self):
        User.objects.filter(username="demo_alice").delete()
        with self.assertRaisesMessage(simulator.SimulationError, "demo_alice"):
            simulator.run(self.live_server_url, interval=0, log=lambda m: None)

    def test_clear_error_when_server_is_not_running(self):
        with self.assertRaisesMessage(simulator.SimulationError, "Is `python manage.py runserver` running?"):
            simulator.run("http://127.0.0.1:9", interval=0, log=lambda m: None)

    def test_wrong_employer_credentials_do_not_break_the_run(self):
        s = simulator.run(self.live_server_url, interval=0, employer=("nobody", "x"), log=lambda m: None)
        self.assertEqual(s["dashboard"], [])
        self.assertTrue(any("employer sign-in" in e for e in s["events"]))
