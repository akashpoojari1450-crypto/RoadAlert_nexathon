from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Company, TrackingPolicy, User
from incidents.verification import submit_report
from journeys.models import GpsPoint, Journey

LAT, LNG = 12.8160, 74.8500


class PrivacyAndDashboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.company = Company.objects.create(name="Acme")
        self.other_company = Company.objects.create(name="Other")
        TrackingPolicy.objects.create(company=self.company, scope=TrackingPolicy.Scope.JOURNEY_STARTED)
        self.employer = User.objects.create_user("boss", password="x", role=User.Role.EMPLOYER, company=self.company)
        self.emp = User.objects.create_user("emp1", password="x", role=User.Role.EMPLOYEE, company=self.company)

    def _start(self, user, **extra):
        self.client.force_authenticate(user)
        data = {"kind": "commute", "origin_name": "Mangaluru", "destination_name": "Udupi",
                "origin_lat": LAT, "origin_lng": LNG, "dest_lat": LAT + 0.5, "dest_lng": LNG}
        data.update(extra)
        return self.client.post("/api/journeys/start/", data, format="json")

    def _grant(self):
        self.client.force_authenticate(self.emp)
        self.client.post("/api/auth/consent/", {"granted": True}, format="json")

    def test_no_consent_means_not_shared(self):
        r = self._start(self.emp)
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()["tracking_visible_to_employer"])

    def test_consent_means_shared_and_employee_is_told(self):
        self._grant()
        r = self._start(self.emp)
        self.assertTrue(r.json()["tracking_visible_to_employer"])

    def test_personal_journey_never_shared(self):
        self._grant()
        self.assertFalse(self._start(self.emp, kind="personal").json()["tracking_visible_to_employer"])

    def test_policy_scope_commute_only(self):
        self._grant()
        TrackingPolicy.objects.filter(company=self.company).update(scope="commute")
        self.assertTrue(self._start(self.emp, kind="commute").json()["tracking_visible_to_employer"])
        self.assertFalse(self._start(self.emp, kind="official_travel").json()["tracking_visible_to_employer"])

    def test_no_policy_means_not_shared(self):
        self._grant()
        TrackingPolicy.objects.all().delete()
        self.assertFalse(self._start(self.emp).json()["tracking_visible_to_employer"])

    def test_revoking_consent_hides_existing_journeys(self):
        self._grant()
        self._start(self.emp)
        self.client.force_authenticate(self.employer)
        self.assertEqual(len(self.client.get("/api/employer/dashboard/").json()), 1)
        self.client.force_authenticate(self.emp)
        self.client.post("/api/auth/consent/", {"granted": False}, format="json")
        self.client.force_authenticate(self.employer)
        self.assertEqual(self.client.get("/api/employer/dashboard/").json(), [])

    def test_passenger_cannot_grant_consent(self):
        p = User.objects.create_user("p", password="x")
        self.client.force_authenticate(p)
        self.assertEqual(self.client.post("/api/auth/consent/", {"granted": True}, format="json").status_code, 400)

    def test_employer_cannot_see_other_company(self):
        outsider = User.objects.create_user("o", password="x", role=User.Role.EMPLOYEE, company=self.other_company,
                                            tracking_consent=True)
        TrackingPolicy.objects.create(company=self.other_company)
        self._start(outsider)
        self.client.force_authenticate(self.employer)
        self.assertEqual(self.client.get("/api/employer/dashboard/").json(), [])

    def test_dashboard_forbidden_for_employee_and_passenger(self):
        for u in (self.emp, User.objects.create_user("p", password="x")):
            self.client.force_authenticate(u)
            self.assertEqual(self.client.get("/api/employer/dashboard/").status_code, 403)

    def test_cannot_post_gps_to_someone_elses_journey(self):
        self._grant()
        jid = self._start(self.emp).json()["id"]
        self.client.force_authenticate(self.employer)
        r = self.client.post(f"/api/journeys/{jid}/points/", {"points": [{"latitude": 1, "longitude": 1}]}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_no_gps_after_journey_ends(self):
        jid = self._start(self.emp).json()["id"]
        self.client.post(f"/api/journeys/{jid}/end/")
        r = self.client.post(f"/api/journeys/{jid}/points/", {"points": [{"latitude": 1, "longitude": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_gps_batch_validation(self):
        jid = self._start(self.emp).json()["id"]
        r = self.client.post(f"/api/journeys/{jid}/points/", {"points": [{"latitude": 500, "longitude": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_dashboard_end_to_end_traffic_row_with_evidence(self):
        """The PDF scenario: employee crawls at a junction, employer sees verified delay + evidence."""
        self._grant()
        jid = self._start(self.emp).json()["id"]
        now = timezone.now()
        pts = [{"latitude": LAT, "longitude": LNG, "speed_kmh": 2,
                "recorded_at": (now - timedelta(minutes=35 - i)).isoformat()} for i in range(36)]
        self.assertEqual(self.client.post(f"/api/journeys/{jid}/points/", {"points": pts}, format="json").status_code, 201)
        submit_report(reporter=self.emp, incident_type="heavy_traffic", lat=LAT, lng=LNG, road_name="NH-66 - Thokkotu")

        self.client.force_authenticate(self.employer)
        rows = self.client.get("/api/employer/dashboard/").json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["employee"], "emp1")
        self.assertEqual(row["route"], "Mangaluru - Udupi")
        self.assertEqual(row["status"], "Traffic")
        self.assertEqual(row["delay_min"], 35)
        self.assertIn("Thokkotu", row["evidence"])
        self.assertIn("remained in the affected zone", row["evidence"])

    def test_dashboard_normal_row(self):
        self._grant()
        jid = self._start(self.emp).json()["id"]
        self.client.post(f"/api/journeys/{jid}/points/", {"points": [
            {"latitude": LAT, "longitude": LNG, "speed_kmh": 50}]}, format="json")
        self.client.force_authenticate(self.employer)
        row = self.client.get("/api/employer/dashboard/").json()[0]
        self.assertEqual((row["status"], row["delay_min"], row["verification"]), ("Normal", 0, "GPS"))

    def test_employer_adds_employee_without_consent(self):
        self.client.force_authenticate(self.employer)
        r = self.client.post("/api/auth/employees/", {"username": "newbie", "password": "longpass123"}, format="json")
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(username="newbie")
        self.assertEqual((u.role, u.company, u.tracking_consent), ("employee", self.company, False))

    def test_only_employer_sets_policy(self):
        self.client.force_authenticate(self.emp)
        self.assertEqual(self.client.put("/api/auth/policy/", {"scope": "commute"}, format="json").status_code, 403)
        self.client.force_authenticate(self.employer)
        r = self.client.put("/api/auth/policy/", {"scope": "commute"}, format="json")
        self.assertEqual(r.json()["scope"], "commute")


class EmployerRowsRegressionTests(TestCase):
    """Bugs found by looking at the real dashboard page."""

    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        self.emp = User.objects.create_user("emp", password="x", role=User.Role.EMPLOYEE, company=self.company,
                                            tracking_consent=True)

    def _journey(self, speed, minutes=10):
        j = Journey.objects.create(user=self.emp, origin_lat=LAT, origin_lng=LNG, dest_lat=LAT + 0.1,
                                   dest_lng=LNG, shared_with_employer=True, origin_name="A", destination_name="B")
        now = timezone.now()
        GpsPoint.objects.bulk_create([
            GpsPoint(journey=j, latitude=LAT, longitude=LNG, speed_kmh=speed,
                     recorded_at=now - timedelta(minutes=minutes - i)) for i in range(minutes + 1)])
        return j

    def _incident(self, **kw):
        from incidents.models import Incident
        d = dict(incident_type="flood", latitude=LAT, longitude=LNG, started_at=timezone.now() - timedelta(days=5),
                 verification_level=3, road_name="Old spot")
        d.update(kw)
        return Incident.objects.create(**d)

    def test_old_resolved_incident_is_not_attached_to_a_normal_trip(self):
        from journeys.views import employer_rows
        self._incident(resolved_at=timezone.now() - timedelta(days=4))
        self._journey(speed=50)
        row = employer_rows(self.company)[0]
        self.assertEqual((row["status"], row["verification"], row["evidence"]), ("Normal", "GPS", ""))

    def test_old_resolved_incident_does_not_explain_a_new_jam(self):
        from journeys.views import employer_rows
        self._incident(resolved_at=timezone.now() - timedelta(days=4), incident_type="road_closure")
        self._journey(speed=2)
        row = employer_rows(self.company)[0]
        self.assertEqual(row["status"], "Traffic")  # not "Road Block"
        self.assertEqual(row["verification"], "GPS speed data")

    def test_live_incident_explains_a_jam(self):
        from journeys.views import employer_rows
        self._incident(incident_type="road_closure", resolved_at=None)
        self._journey(speed=2)
        row = employer_rows(self.company)[0]
        self.assertEqual(row["status"], "Road Block")
        self.assertIn("Old spot", row["evidence"])

    def test_incident_near_a_normal_trip_does_not_change_its_status(self):
        from journeys.views import employer_rows
        self._incident(incident_type="road_closure", resolved_at=None)
        self._journey(speed=50)
        self.assertEqual(employer_rows(self.company)[0]["status"], "Normal")

    def test_jam_in_the_middle_of_a_route_is_found(self):
        from incidents.models import Incident
        from journeys.views import employer_rows
        far = (LAT + 0.2, LNG)
        j = Journey.objects.create(user=self.emp, origin_lat=LAT, origin_lng=LNG, dest_lat=far[0], dest_lng=far[1],
                                   shared_with_employer=True)
        now = timezone.now()
        mid = (LAT + 0.1, LNG)
        pts = [(LAT, LNG, 50, 30), (mid[0], mid[1], 2, 20), (mid[0], mid[1], 2, 12), (far[0], far[1], 50, 1)]
        GpsPoint.objects.bulk_create([GpsPoint(journey=j, latitude=a, longitude=b, speed_kmh=s,
                                               recorded_at=now - timedelta(minutes=m)) for a, b, s, m in pts])
        Incident.objects.create(incident_type="accident", road_name="Mid-route", latitude=mid[0], longitude=mid[1],
                                started_at=now - timedelta(minutes=30), verification_level=3)
        row = employer_rows(self.company)[0]
        self.assertEqual(row["status"], "Traffic")
        self.assertIn("Mid-route", row["evidence"])
