from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from incidents.models import Incident, VerificationLevel
from incidents.verification import haversine_m, refresh_incident, submit_report
from journeys.models import GpsPoint, Journey

# Thokkotu Junction, Mangaluru (approx.)
LAT, LNG = 12.8160, 74.8500


def make_user(name, role=User.Role.PASSENGER, **kw):
    return User.objects.create_user(username=name, password="pass12345", role=role, **kw)


def add_trail(user, minutes=10, speed=2.0, lat=LAT, lng=LNG):
    """A journey whose GPS trail sits at the incident spot, crawling for `minutes`."""
    j = Journey.objects.create(user=user, origin_lat=lat, origin_lng=lng, dest_lat=lat + 0.1, dest_lng=lng)
    now = timezone.now()
    GpsPoint.objects.bulk_create([
        GpsPoint(journey=j, latitude=lat, longitude=lng, speed_kmh=speed,
                 recorded_at=now - timedelta(minutes=minutes - i))
        for i in range(minutes + 1)
    ])
    return j


class HaversineTests(TestCase):
    def test_known_distance(self):
        # ~0.001 degrees of latitude is ~111 m
        self.assertAlmostEqual(haversine_m(LAT, LNG, LAT + 0.001, LNG), 111.2, delta=1)

    def test_zero(self):
        self.assertEqual(haversine_m(LAT, LNG, LAT, LNG), 0)


class MatchingTests(TestCase):
    def test_nearby_same_type_merges(self):
        a, b = make_user("a"), make_user("b")
        i1, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        i2, *_ = submit_report(reporter=b, incident_type="accident", lat=LAT + 0.001, lng=LNG)  # ~111 m
        self.assertEqual(i1.id, i2.id)
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(i2.reports.count(), 2)

    def test_far_or_different_type_does_not_merge(self):
        a = make_user("a")
        i1, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        i2, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT + 0.02, lng=LNG)  # ~2 km
        i3, *_ = submit_report(reporter=a, incident_type="flood", lat=LAT, lng=LNG)
        self.assertEqual(len({i1.id, i2.id, i3.id}), 3)

    def test_resolved_incident_not_reused(self):
        a = make_user("a")
        i1, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        i1.resolved_at = timezone.now()
        i1.save()
        i2, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        self.assertNotEqual(i1.id, i2.id)


class VerificationLevelTests(TestCase):
    def test_level1_user_reported_only(self):
        inc, _, res = submit_report(reporter=make_user("a"), incident_type="accident", lat=LAT, lng=LNG)
        self.assertEqual(res.level, VerificationLevel.USER_REPORTED)
        self.assertEqual(res.evidence, ["user_reported"])

    def test_level2_reporter_gps_at_location(self):
        a = make_user("a")
        add_trail(a, minutes=1, speed=40)  # moving, so no speed confirmation
        inc, _, res = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        self.assertEqual(res.level, VerificationLevel.GPS_CONFIRMED)
        self.assertIn("gps_confirmed", res.evidence)

    def test_someone_elses_trail_is_not_reporter_gps(self):
        reporter, other = make_user("a"), make_user("b")
        add_trail(other, minutes=1, speed=40)
        _, _, res = submit_report(reporter=reporter, incident_type="accident", lat=LAT, lng=LNG)
        self.assertNotIn("gps_confirmed", res.evidence)

    def test_level3_multiple_distinct_users(self):
        users = [make_user(f"u{i}") for i in range(3)]
        for u in users:
            inc, _, res = submit_report(reporter=u, incident_type="accident", lat=LAT, lng=LNG)
        self.assertGreaterEqual(res.level, VerificationLevel.MULTIPLE_USERS)
        self.assertIn("multiple_users", res.evidence)

    def test_same_user_repeating_does_not_count_as_multiple(self):
        a = make_user("a")
        for _ in range(5):
            _, _, res = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        self.assertNotIn("multiple_users", res.evidence)
        self.assertEqual(res.distinct_reporters, 1)

    def test_level4_speed_data_and_delay(self):
        a = make_user("a")
        add_trail(a, minutes=32, speed=2)  # the "Thokkotu 2 km/h for 32 minutes" example
        inc, _, res = submit_report(reporter=a, incident_type="heavy_traffic", lat=LAT, lng=LNG)
        self.assertEqual(res.level, VerificationLevel.SPEED_CONFIRMED)
        self.assertEqual(res.estimated_delay_min, 32)
        self.assertEqual(res.severity, "severe")
        inc.refresh_from_db()
        self.assertEqual(inc.estimated_delay_min, 32)

    def test_fast_trail_is_not_congestion(self):
        a = make_user("a")
        add_trail(a, minutes=20, speed=45)
        _, _, res = submit_report(reporter=a, incident_type="heavy_traffic", lat=LAT, lng=LNG)
        self.assertNotIn("speed_confirmed", res.evidence)
        self.assertEqual(res.estimated_delay_min, 0)

    def test_short_slowdown_is_not_congestion(self):
        a = make_user("a")
        add_trail(a, minutes=2, speed=2)  # under the 5-minute minimum
        _, _, res = submit_report(reporter=a, incident_type="heavy_traffic", lat=LAT, lng=LNG)
        self.assertNotIn("speed_confirmed", res.evidence)

    def test_level5_media(self):
        inc, report, _ = submit_report(reporter=make_user("a"), incident_type="flood", lat=LAT, lng=LNG)
        report.photo = "reports/photos/x.jpg"
        report.save()
        res = refresh_incident(inc)
        self.assertEqual(res.level, VerificationLevel.MEDIA_EVIDENCE)

    def test_level6_authority_overrides(self):
        inc, *_ = submit_report(reporter=make_user("a"), incident_type="accident", lat=LAT, lng=LNG)
        inc.authority_confirmed = True
        inc.save()
        res = refresh_incident(inc)
        self.assertEqual(res.level, VerificationLevel.AUTHORITY_CONFIRMED)
        inc.refresh_from_db()
        self.assertEqual(inc.confidence, 100)

    def test_authority_confirmation_without_user_reports_is_kept(self):
        """Regression: an incident with no user reports used to be reset to Level 1 / 0%."""
        inc = Incident.objects.create(incident_type="accident", latitude=LAT, longitude=LNG,
                                      started_at=timezone.now(), severity="heavy", authority_confirmed=True)
        res = refresh_incident(inc)
        inc.refresh_from_db()
        self.assertEqual((inc.verification_level, inc.confidence, inc.severity), (6, 100, "heavy"))
        self.assertEqual(res.evidence, ["authority_confirmed"])

    def test_unconfirmed_incident_without_reports_keeps_severity(self):
        inc = Incident.objects.create(incident_type="flood", latitude=LAT, longitude=LNG,
                                      started_at=timezone.now(), severity="severe")
        refresh_incident(inc)
        inc.refresh_from_db()
        self.assertEqual((inc.severity, inc.verification_level), ("severe", 1))

    def test_confidence_rises_with_evidence(self):
        a = make_user("a")
        _, _, r1 = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        for i in range(3):
            u = make_user(f"x{i}")
            add_trail(u, minutes=10, speed=3)
            _, _, r2 = submit_report(reporter=u, incident_type="accident", lat=LAT, lng=LNG)
        self.assertGreater(r2.confidence, r1.confidence)
        self.assertLessEqual(r2.confidence, 99)


class IncidentApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _login(self, user):
        self.client.force_authenticate(user)

    def test_auth_required(self):
        self.assertEqual(self.client.get("/api/incidents/nearby/?lat=1&lng=1").status_code, 401)

    def test_register_login_report_flow(self):
        r = self.client.post("/api/auth/register/", {"username": "asha", "password": "longpass123"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["user"]["role"], "passenger")
        r = self.client.post("/api/auth/login/", {"username": "asha", "password": "longpass123"})
        self.client.credentials(HTTP_AUTHORIZATION="Token " + r.json()["token"])
        r = self.client.post("/api/incidents/report/", {
            "incident_type": "accident", "latitude": LAT, "longitude": LNG, "road_name": "NH-66 - Thokkotu",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["verification_level"], 1)
        self.assertEqual(body["reports"], 1)

    def test_cannot_self_register_as_employer(self):
        r = self.client.post("/api/auth/register/", {"username": "x", "password": "longpass123", "role": "admin"})
        self.assertEqual(r.json()["user"]["role"], "passenger")

    def test_invalid_report_rejected(self):
        self._login(make_user("a"))
        r = self.client.post("/api/incidents/report/", {"incident_type": "nonsense", "latitude": 999, "longitude": 0}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_nearby_sorted_and_filtered(self):
        a = make_user("a")
        submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        submit_report(reporter=a, incident_type="flood", lat=LAT + 0.01, lng=LNG)      # ~1.1 km
        submit_report(reporter=a, incident_type="landslide", lat=LAT + 0.5, lng=LNG)   # far away
        self._login(a)
        rows = self.client.get(f"/api/incidents/nearby/?lat={LAT}&lng={LNG}&radius_m=3000").json()
        self.assertEqual([r["incident_type"] for r in rows], ["accident", "flood"])
        self.assertLess(rows[0]["distance_m"], rows[1]["distance_m"])

    def test_nearby_requires_coordinates(self):
        self._login(make_user("a"))
        self.assertEqual(self.client.get("/api/incidents/nearby/").status_code, 400)

    def test_only_authority_can_confirm(self):
        inc, *_ = submit_report(reporter=make_user("a"), incident_type="accident", lat=LAT, lng=LNG)
        self._login(make_user("p"))
        self.assertEqual(self.client.post(f"/api/incidents/{inc.id}/confirm/").status_code, 403)
        self._login(make_user("auth", role=User.Role.AUTHORITY))
        r = self.client.post(f"/api/incidents/{inc.id}/confirm/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["verification_level"], 6)

    def test_resolved_incident_leaves_map(self):
        a = make_user("a")
        inc, *_ = submit_report(reporter=a, incident_type="accident", lat=LAT, lng=LNG)
        self._login(make_user("auth", role=User.Role.AUTHORITY))
        self.client.post(f"/api/incidents/{inc.id}/resolve/")
        self.assertEqual(self.client.get(f"/api/incidents/nearby/?lat={LAT}&lng={LNG}").json(), [])


class MonthlyReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        a = make_user("a")
        now = timezone.now()
        for kind, delay, road in [("accident", 60, "NH-66 - Pumpwell"), ("accident", 30, "NH-66 - Pumpwell"),
                                  ("flood", 90, "Kottara")]:
            Incident.objects.create(incident_type=kind, latitude=LAT, longitude=LNG, started_at=now,
                                    estimated_delay_min=delay, road_name=road)

    def test_report_aggregates(self):
        self.client.force_authenticate(make_user("auth", role=User.Role.AUTHORITY))
        d = self.client.get("/api/incidents/reports/monthly/").json()
        self.assertEqual(d["total_incidents"], 3)
        self.assertEqual(d["total_delay_hours"], 3.0)
        self.assertEqual(d["max_delay_min"], 90)
        self.assertEqual(d["most_frequent_reason"], "Accident")
        self.assertEqual(d["by_reason"][0], {"reason": "Accident", "incidents": 2, "total_delay_hours": 1.5})
        self.assertEqual(d["hotspots"][0]["road"], "NH-66 - Pumpwell")

    def test_passenger_forbidden(self):
        self.client.force_authenticate(make_user("p"))
        self.assertEqual(self.client.get("/api/incidents/reports/monthly/").status_code, 403)

    def test_empty_month(self):
        self.client.force_authenticate(make_user("auth", role=User.Role.AUTHORITY))
        d = self.client.get("/api/incidents/reports/monthly/?year=2020&month=1").json()
        self.assertEqual(d["total_incidents"], 0)
        self.assertIsNone(d["most_frequent_reason"])

    def test_december_rollover(self):
        self.client.force_authenticate(make_user("auth", role=User.Role.AUTHORITY))
        self.assertEqual(self.client.get("/api/incidents/reports/monthly/?year=2026&month=12").status_code, 200)

    def test_bad_month(self):
        self.client.force_authenticate(make_user("auth", role=User.Role.AUTHORITY))
        self.assertEqual(self.client.get("/api/incidents/reports/monthly/?month=13").status_code, 400)
