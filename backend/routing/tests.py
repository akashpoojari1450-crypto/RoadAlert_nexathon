from django.test import SimpleTestCase, TestCase, override_settings
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from incidents.geo import distance_to_route_m, route_progress_m, valid_geometry
from incidents.models import Incident
from routing.providers import FakeProvider, RoutingError

# Mangaluru -> Udupi (roughly 50 km apart)
ORIGIN = {"lat": 12.87, "lng": 74.84}
DEST = {"lat": 13.34, "lng": 74.75}
MID = ((12.87 + 13.34) / 2, (74.84 + 74.75) / 2)  # on the FakeProvider's direct route


class BrokenProvider:
    def routes(self, origin, destination):
        raise RoutingError("Routing service unavailable: boom")


def make_incident(delay=45, resolved=False, **kw):
    defaults = dict(
        incident_type="accident", road_name="NH-66 - Mulki", latitude=MID[0], longitude=MID[1],
        severity="heavy", confidence=92, verification_level=3, estimated_delay_min=delay,
        started_at=timezone.now(),
    )
    defaults.update(kw)
    if resolved:
        defaults["resolved_at"] = timezone.now()
    return Incident.objects.create(**defaults)


class GeoTests(SimpleTestCase):
    LINE = [[74.0, 12.0], [74.02, 12.0]]  # ~2.2 km east-west

    def test_distance_beside_line(self):
        d = distance_to_route_m(12.001, 74.01, self.LINE)  # ~111 m north of the middle
        self.assertAlmostEqual(d, 111, delta=3)

    def test_distance_on_line_is_zero(self):
        self.assertAlmostEqual(distance_to_route_m(12.0, 74.01, self.LINE), 0, delta=0.5)

    def test_beyond_endpoint_measures_to_endpoint(self):
        d = distance_to_route_m(12.0, 74.03, self.LINE)  # ~1.1 km past the end
        self.assertAlmostEqual(d, 1070, delta=40)

    def test_empty_and_single_point(self):
        self.assertEqual(distance_to_route_m(1, 1, []), float("inf"))
        self.assertGreater(distance_to_route_m(12.001, 74.0, [[74.0, 12.0]]), 100)

    def test_valid_geometry(self):
        self.assertTrue(valid_geometry([[74.0, 12.0], [74.1, 12.1]]))
        for bad in ([], [[74.0, 12.0]], "x", [[74.0], [1, 2]], [[200, 0], [0, 0]], [[0, 95], [0, 0]],
                    [["a", "b"], [0, 0]], [[True, False], [0, 0]], None):
            self.assertFalse(valid_geometry(bad), bad)


class RouteProgressTests(SimpleTestCase):
    LINE = [[74.0, 12.0], [74.02, 12.0]]            # 2.2 km east
    BENT = [[74.0, 12.0], [74.01, 12.0], [74.01, 12.01]]   # east 1.1 km, then north 1.1 km

    def test_progress_increases_along_route(self):
        a = route_progress_m(12.0, 74.0, self.LINE)
        b = route_progress_m(12.0, 74.01, self.LINE)
        c = route_progress_m(12.0, 74.02, self.LINE)
        self.assertAlmostEqual(a, 0, delta=1)
        self.assertAlmostEqual(b, 1090, delta=30)
        self.assertAlmostEqual(c, 2180, delta=60)

    def test_point_beside_route_projects_onto_it(self):
        self.assertAlmostEqual(route_progress_m(12.002, 74.01, self.LINE), 1090, delta=30)

    def test_clamped_before_start_and_after_end(self):
        self.assertAlmostEqual(route_progress_m(12.0, 73.99, self.LINE), 0, delta=1)
        self.assertAlmostEqual(route_progress_m(12.0, 74.05, self.LINE), 2180, delta=60)

    def test_bent_route_counts_both_legs(self):
        end = route_progress_m(12.01, 74.01, self.BENT)
        self.assertAlmostEqual(end, 2180, delta=60)
        self.assertGreater(end, route_progress_m(12.0, 74.01, self.BENT))


class FakeProviderTests(SimpleTestCase):
    def test_two_routes_detour_longer(self):
        direct, detour = FakeProvider().routes((12.87, 74.84), (13.34, 74.75))
        self.assertGreater(detour.distance_m, direct.distance_m)
        self.assertGreater(detour.duration_s, direct.duration_s)
        self.assertEqual(len(direct.coordinates), len(detour.coordinates))

    def test_same_point_errors(self):
        with self.assertRaises(RoutingError):
            FakeProvider().routes((12.87, 74.84), (12.87, 74.84))


class PlanTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("u", password="x"))

    def plan(self):
        return self.client.post("/api/routes/plan/", {"origin": ORIGIN, "destination": DEST}, format="json")

    def test_requires_auth(self):
        self.assertEqual(APIClient().post("/api/routes/plan/", {}, format="json").status_code, 401)

    def test_clear_roads_no_alert(self):
        d = self.plan().json()
        self.assertIsNone(d["alert"])
        self.assertEqual(d["recommended"], 0)
        self.assertEqual(len(d["routes"]), 2)
        self.assertNotIn("_incidents", d["routes"][0])

    def test_incident_on_route_raises_alert_and_recommends_detour(self):
        inc = make_incident(delay=45)
        d = self.plan().json()
        self.assertEqual(d["routes"][0]["incidents"], [inc.id])
        self.assertEqual(d["routes"][1]["incidents"], [])
        self.assertEqual(d["recommended"], 1)
        a = d["alert"]
        self.assertEqual(a["incident_id"], inc.id)
        self.assertEqual(a["estimated_delay_min"], 45)
        self.assertEqual(a["possible_reason"], "Accident")
        self.assertEqual(a["confidence"], 92)
        self.assertTrue(a["alternative_available"])
        self.assertIn("Mulki", a["title"])

    def test_small_delay_does_not_justify_detour(self):
        make_incident(delay=5)
        d = self.plan().json()
        self.assertIsNotNone(d["alert"])
        self.assertFalse(d["alert"]["alternative_available"])
        self.assertEqual(d["recommended"], 0)

    def test_resolved_incident_ignored(self):
        make_incident(resolved=True)
        self.assertIsNone(self.plan().json()["alert"])

    def test_far_incident_ignored(self):
        make_incident(latitude=MID[0], longitude=MID[1] + 0.05)  # ~5 km off the route, outside the detour too
        self.assertIsNone(self.plan().json()["alert"])

    def test_validation(self):
        r = self.client.post("/api/routes/plan/", {"origin": {"lat": 999, "lng": 0}, "destination": DEST}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.post("/api/routes/plan/", {}, format="json").status_code, 400)

    def test_same_origin_and_destination_is_503_with_message(self):
        r = self.client.post("/api/routes/plan/", {"origin": ORIGIN, "destination": ORIGIN}, format="json")
        self.assertEqual(r.status_code, 503)
        self.assertIn("same place", r.json()["detail"])

    def test_provider_outage_is_503(self):
        with override_settings(ROUTING={**settings.ROUTING, "PROVIDER": "routing.tests.BrokenProvider"}):
            r = self.plan()
        self.assertEqual(r.status_code, 503)
        self.assertIn("unavailable", r.json()["detail"])


class AvoidTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("u", password="x"))
        self.inc = make_incident(delay=45)

    def avoid(self, incident_id=None):
        return self.client.post("/api/routes/avoid/", {
            "origin": ORIGIN, "destination": DEST, "incident_id": incident_id or self.inc.id,
        }, format="json")

    def test_returns_route_clear_of_incident(self):
        d = self.avoid().json()
        self.assertTrue(d["avoids_incident"])
        self.assertEqual(d["route"]["index"], 1)
        self.assertNotIn(self.inc.id, d["route"]["incidents"])
        self.assertNotIn("_incidents", d["route"])

    def test_honest_when_no_route_is_clear(self):
        with override_settings(ROUTING={**settings.ROUTING, "AVOID_RADIUS_M": 5000}):
            d = self.avoid().json()
        self.assertFalse(d["avoids_incident"])

    def test_unknown_incident_404(self):
        self.assertEqual(self.avoid(incident_id=99999).status_code, 404)
