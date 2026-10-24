"""Decide who needs to hear about an incident, and tell them."""
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from accounts.models import User
from incidents.geo import distance_to_route_m, route_progress_m
from incidents.models import Severity, VerificationLevel
from journeys.models import Journey

from .models import Notification
from .push import send_push

log = logging.getLogger(__name__)


def should_alert(incident):
    """Only alert on things worth interrupting people for."""
    if incident.resolved_at is not None:
        return False
    return (
        incident.severity in (Severity.HEAVY, Severity.SEVERE, Severity.CLOSED)
        or incident.verification_level >= VerificationLevel.MULTIPLE_USERS
    )


def _create_once(user, kind, title, body, incident):
    """Create a notification unless this user already has one for this incident+kind."""
    try:
        with transaction.atomic():
            note = Notification.objects.create(user=user, kind=kind, title=title, body=body, incident=incident)
    except IntegrityError:
        return None
    send_push(user, title, body, {"incident_id": incident.id, "kind": kind})
    return note


def _position(journey):
    """Where the traveller is now: their latest GPS point, or the journey's origin."""
    last = journey.points.order_by("-recorded_at").first()
    return (last.latitude, last.longitude) if last else (journey.origin_lat, journey.origin_lng)


def affected_journeys(incident):
    """
    Active journeys whose route passes the incident AND who have not reached it yet.
    Someone already stuck in the jam, or past it, gains nothing from a warning.
    """
    radius = settings.ROUTING["ROUTE_ALERT_RADIUS_M"]
    margin = settings.ROUTING["ALERT_MIN_AHEAD_M"]
    reporter_ids = set(incident.reports.values_list("reporter_id", flat=True))
    hits = []
    # MVP: scan active journeys. At scale, prefilter by a stored route bounding box (or PostGIS).
    for j in Journey.objects.filter(status=Journey.Status.ACTIVE).select_related("user"):
        if j.user_id in reporter_ids or not j.route_geometry:
            continue  # reporters already know about it
        if distance_to_route_m(incident.latitude, incident.longitude, j.route_geometry) > radius:
            continue
        here = route_progress_m(*_position(j), j.route_geometry)
        ahead = route_progress_m(incident.latitude, incident.longitude, j.route_geometry)
        if ahead - here >= margin:
            hits.append(j)
    return hits


def dispatch_incident_alerts(incident):
    """Traffic alerts to affected travellers, and aggregate counts to their employers."""
    if not should_alert(incident):
        return 0
    journeys = affected_journeys(incident)
    place = incident.road_name or "your route"
    sent = 0

    for j in journeys:
        delay = f" Expected delay {incident.estimated_delay_min} min." if incident.estimated_delay_min else ""
        title = f"{incident.get_severity_display()} traffic near {place}"
        body = (f"{incident.get_incident_type_display()} reported on your route.{delay} "
                f"Consider an alternative route.")
        if _create_once(j.user, Notification.Kind.TRAFFIC_ALERT, title, body, incident):
            sent += 1

    # Employer alert: only journeys the employee agreed to share, and only a count,
    # never names. (Names stay on the dashboard, governed by the same consent rules.)
    by_company = {}
    for j in journeys:
        if j.shared_with_employer and j.user.company_id:
            by_company[j.user.company_id] = by_company.get(j.user.company_id, 0) + 1
    for company_id, count in by_company.items():
        noun = "employee" if count == 1 else "employees"
        for employer in User.objects.filter(company_id=company_id, role=User.Role.EMPLOYER):
            _create_once(
                employer, Notification.Kind.EMPLOYER_ALERT, "Employee travel alert",
                f"{count} {noun} travelling through the affected route near {place} may experience delays.",
                incident,
            )
    return sent
