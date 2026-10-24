from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Company, User
from incidents.models import Incident
from incidents.verification import submit_report
from journeys.models import Journey
from notifications.models import DeviceToken, Notification
from notifications.push import LoggingSender
from notifications.services import dispatch_incident_alerts

LAT, LNG = 12.9000, 74.8400
# A north-south route passing straight through the incident
ROUTE = [[LNG, LAT - 0.05], [LNG, LAT], [LNG, LAT + 0.05]]
FAR_ROUTE = [[LNG + 0.1, LAT - 0.05], [LNG + 0.1, LAT + 0.05]]  # ~11 km east


class ExplodingSender:
    def send(self, tokens, title, body, data):
        raise RuntimeError("FCM is down")


def user(name, **kw):
    return User.objects.create_user(name, password="x", **kw)


def journey(u, route=ROUTE, shared=False, status=Journey.Status.ACTIVE):
    return Journey.objects.create(
        user=u, origin_lat=LAT - 0.05, origin_lng=LNG, dest_lat=LAT + 0.05, dest_lng=LNG,
        route_geometry=route, shared_with_employer=shared, status=status,
    )


def heavy_incident(**kw):
    d = dict(incident_type="accident", road_name="NH-66 - Pumpwell", latitude=LAT, longitude=LNG,
             severity="heavy", verification_level=3, confidence=90, estimated_delay_min=30,
             started_at=timezone.now())
    d.update(kw)
    return Incident.objects.create(**d)


class AlertTargetingTests(TestCase):
    def setUp(self):
        LoggingSender.sent.clear()
        self.traveller = user("trav")
        DeviceToken.objects.create(user=self.traveller, token="tok-trav-123456")

    def test_traveller_on_route_is_notified_and_pushed(self):
        journey(self.traveller)
        n = dispatch_incident_alerts(heavy_incident())
        self.assertEqual(n, 1)
        note = Notification.objects.get(user=self.traveller)
        self.assertEqual(note.kind, "traffic_alert")
        self.assertIn("Pumpwell", note.title)
        self.assertIn("alternative route", note.body)
        self.assertIn("30 min", note.body)
        self.assertEqual(len(LoggingSender.sent), 1)
        self.assertEqual(LoggingSender.sent[0][0], "tok-trav-123456")

    def test_not_notified_when_route_elsewhere_or_journey_over_or_no_route(self):
        journey(self.traveller, route=FAR_ROUTE)
        journey(self.traveller, status=Journey.Status.COMPLETED)
        journey(self.traveller, route=[])
        self.assertEqual(dispatch_incident_alerts(heavy_incident()), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_reporters_are_not_alerted_about_their_own_report(self):
        journey(self.traveller)
        inc, *_ = submit_report(reporter=self.traveller, incident_type="accident", lat=LAT, lng=LNG)
        inc.severity, inc.verification_level = "heavy", 3
        inc.save()
        self.assertEqual(dispatch_incident_alerts(inc), 0)

    def test_dedup_one_alert_per_incident(self):
        journey(self.traveller)
        inc = heavy_incident()
        dispatch_incident_alerts(inc)
        dispatch_incident_alerts(inc)
        self.assertEqual(Notification.objects.filter(user=self.traveller).count(), 1)
        self.assertEqual(len(LoggingSender.sent), 1)

    def test_minor_or_resolved_incidents_do_not_alert(self):
        journey(self.traveller)
        self.assertEqual(dispatch_incident_alerts(heavy_incident(severity="moderate", verification_level=1)), 0)
        self.assertEqual(dispatch_incident_alerts(heavy_incident(resolved_at=timezone.now())), 0)

    def test_full_path_three_reports_trigger_alert_automatically(self):
        journey(self.traveller)
        for i in range(3):
            submit_report(reporter=user(f"r{i}"), incident_type="accident", lat=LAT, lng=LNG,
                          road_name="NH-66 - Pumpwell")
        self.assertEqual(Notification.objects.filter(user=self.traveller, kind="traffic_alert").count(), 1)

    def test_single_unverified_report_does_not_alert(self):
        journey(self.traveller)
        submit_report(reporter=user("r"), incident_type="accident", lat=LAT, lng=LNG)
        self.assertEqual(Notification.objects.count(), 0)


class AheadOfIncidentTests(TestCase):
    """Only people who have not reached the incident yet are worth warning."""

    def setUp(self):
        LoggingSender.sent.clear()
        self.u = user("trav")

    def _at(self, lat, speed=40):
        from journeys.models import GpsPoint
        j = journey(self.u)
        GpsPoint.objects.create(journey=j, latitude=lat, longitude=LNG, speed_kmh=speed, recorded_at=timezone.now())
        return j

    def test_approaching_traveller_is_warned(self):
        self._at(LAT - 0.03)                      # ~3.3 km before the incident
        self.assertEqual(dispatch_incident_alerts(heavy_incident()), 1)

    def test_traveller_already_in_the_jam_is_not_warned(self):
        self._at(LAT - 0.001, speed=2)            # ~100 m before it, inside the queue
        self.assertEqual(dispatch_incident_alerts(heavy_incident()), 0)

    def test_traveller_past_the_incident_is_not_warned(self):
        self._at(LAT + 0.03)                      # ~3.3 km beyond it
        self.assertEqual(dispatch_incident_alerts(heavy_incident()), 0)

    def test_latest_gps_point_decides_not_the_origin(self):
        from journeys.models import GpsPoint
        j = journey(self.u)                       # origin is well before the incident
        GpsPoint.objects.create(journey=j, latitude=LAT - 0.04, longitude=LNG, recorded_at=timezone.now() - timedelta(minutes=5))
        GpsPoint.objects.create(journey=j, latitude=LAT + 0.02, longitude=LNG, recorded_at=timezone.now())  # now past it
        self.assertEqual(dispatch_incident_alerts(heavy_incident()), 0)

    def test_employer_count_excludes_those_already_past(self):
        co = Company.objects.create(name="Co")
        boss = user("boss2", role=User.Role.EMPLOYER, company=co)
        for name, lat in (("ahead", LAT - 0.03), ("stuck", LAT - 0.001), ("past", LAT + 0.03)):
            e = user(name, role=User.Role.EMPLOYEE, company=co)
            from journeys.models import GpsPoint
            j = journey(e, shared=True)
            GpsPoint.objects.create(journey=j, latitude=lat, longitude=LNG, recorded_at=timezone.now())
        dispatch_incident_alerts(heavy_incident())
        self.assertIn("1 employee ", Notification.objects.get(user=boss, kind="employer_alert").body)


class EmployerAlertTests(TestCase):
    def setUp(self):
        LoggingSender.sent.clear()
        self.co, self.other = Company.objects.create(name="Acme"), Company.objects.create(name="Other")
        self.boss = user("boss", role=User.Role.EMPLOYER, company=self.co)
        self.other_boss = user("otherboss", role=User.Role.EMPLOYER, company=self.other)
        self.e1 = user("alice", role=User.Role.EMPLOYEE, company=self.co)
        self.e2 = user("bob", role=User.Role.EMPLOYEE, company=self.co)
        self.e3 = user("carol", role=User.Role.EMPLOYEE, company=self.co)  # not sharing
        journey(self.e1, shared=True)
        journey(self.e2, shared=True)
        journey(self.e3, shared=False)

    def test_employer_gets_count_of_shared_employees_only(self):
        dispatch_incident_alerts(heavy_incident())
        note = Notification.objects.get(user=self.boss, kind="employer_alert")
        self.assertIn("2 employees", note.body)
        self.assertIn("Pumpwell", note.body)

    def test_employer_alert_never_names_employees(self):
        dispatch_incident_alerts(heavy_incident())
        text = " ".join(Notification.objects.filter(user=self.boss).values_list("body", flat=True))
        for name in ("alice", "bob", "carol"):
            self.assertNotIn(name, text)

    def test_other_companies_not_notified(self):
        dispatch_incident_alerts(heavy_incident())
        self.assertFalse(Notification.objects.filter(user=self.other_boss).exists())

    def test_non_sharing_employee_still_gets_own_traffic_alert(self):
        dispatch_incident_alerts(heavy_incident())
        self.assertTrue(Notification.objects.filter(user=self.e3, kind="traffic_alert").exists())

    def test_singular_wording(self):
        Journey.objects.filter(user=self.e2).delete()
        dispatch_incident_alerts(heavy_incident())
        self.assertIn("1 employee ", Notification.objects.get(user=self.boss).body)

    def test_no_sharing_means_no_employer_alert(self):
        Journey.objects.update(shared_with_employer=False)
        dispatch_incident_alerts(heavy_incident())
        self.assertFalse(Notification.objects.filter(user=self.boss).exists())


class ResilienceTests(TestCase):
    def test_push_failure_does_not_lose_notification(self):
        t = user("trav")
        DeviceToken.objects.create(user=t, token="tok-x-1234567")
        journey(t)
        with override_settings(PUSH={**settings.PUSH, "SENDER": "notifications.tests.ExplodingSender"}):
            n = dispatch_incident_alerts(heavy_incident())
        self.assertEqual(n, 1)
        self.assertEqual(Notification.objects.filter(user=t).count(), 1)

    def test_alert_failure_does_not_break_reporting(self):
        with mock.patch("notifications.services.dispatch_incident_alerts", side_effect=RuntimeError("bug")):
            inc, report, res = submit_report(reporter=user("r"), incident_type="accident", lat=LAT, lng=LNG)
        self.assertEqual(inc.reports.count(), 1)


class DeviceAndInboxApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.a, self.b = user("a"), user("b")
        self.client.force_authenticate(self.a)

    def test_register_device(self):
        r = self.client.post("/api/notifications/devices/", {"token": "abc123", "platform": "android"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(DeviceToken.objects.get(token="abc123").user, self.a)

    def test_token_moves_to_new_user_on_shared_phone(self):
        self.client.post("/api/notifications/devices/", {"token": "shared"}, format="json")
        self.client.force_authenticate(self.b)
        self.client.post("/api/notifications/devices/", {"token": "shared"}, format="json")
        self.assertEqual(DeviceToken.objects.get(token="shared").user, self.b)
        self.assertEqual(DeviceToken.objects.count(), 1)

    def test_unregister_on_logout(self):
        self.client.post("/api/notifications/devices/", {"token": "abc"}, format="json")
        self.assertEqual(self.client.delete("/api/notifications/devices/", {"token": "abc"}, format="json").status_code, 204)
        self.assertFalse(DeviceToken.objects.exists())

    def test_cannot_unregister_someone_elses_token(self):
        DeviceToken.objects.create(user=self.b, token="bs")
        self.client.delete("/api/notifications/devices/", {"token": "bs"}, format="json")
        self.assertTrue(DeviceToken.objects.filter(token="bs").exists())

    def test_bad_platform_and_empty_token(self):
        self.assertEqual(self.client.post("/api/notifications/devices/", {"token": "x", "platform": "fax"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/notifications/devices/", {"token": ""}, format="json").status_code, 400)

    def test_inbox_is_private_and_unread_filter_works(self):
        inc = heavy_incident()
        mine = Notification.objects.create(user=self.a, kind="traffic_alert", title="t", body="b", incident=inc)
        Notification.objects.create(user=self.b, kind="traffic_alert", title="secret", body="b", incident=inc)
        rows = self.client.get("/api/notifications/").json()
        self.assertEqual([r["id"] for r in rows], [mine.id])
        self.client.post(f"/api/notifications/{mine.id}/read/")
        self.assertEqual(self.client.get("/api/notifications/?unread=1").json(), [])
        self.assertEqual(len(self.client.get("/api/notifications/").json()), 1)

    def test_cannot_mark_others_notification(self):
        n = Notification.objects.create(user=self.b, kind="traffic_alert", title="t", body="b")
        self.assertEqual(self.client.post(f"/api/notifications/{n.id}/read/").status_code, 404)


class JourneyRouteStorageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.u = user("a")
        self.client.force_authenticate(self.u)

    def start(self, geometry):
        return self.client.post("/api/journeys/start/", {
            "origin_lat": LAT, "origin_lng": LNG, "dest_lat": LAT + 0.1, "dest_lng": LNG, "route_geometry": geometry,
        }, format="json")

    def test_valid_route_stored_but_not_echoed_back(self):
        r = self.start(ROUTE)
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("route_geometry", r.json())
        self.assertEqual(Journey.objects.get(pk=r.json()["id"]).route_geometry, ROUTE)

    def test_invalid_route_rejected(self):
        self.assertEqual(self.start([[1, 2]]).status_code, 400)
        self.assertEqual(self.start("nope").status_code, 400)

    def test_route_is_optional(self):
        r = self.client.post("/api/journeys/start/", {
            "origin_lat": LAT, "origin_lng": LNG, "dest_lat": LAT + 0.1, "dest_lng": LNG}, format="json")
        self.assertEqual(r.status_code, 201)
