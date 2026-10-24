"""
Verification engine: REPORT -> VERIFY.

Given an incident, work out which of the six verification levels have real
evidence behind them, a confidence score (0-100), an estimated delay, and a
severity. Nothing here trusts what a user *says*; it checks GPS trails from
journeys, speeds, how many distinct people report, media and authority input.
"""
import math
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from journeys.models import GpsPoint

from .models import Incident, IncidentReport, IncidentType, Severity, VerificationLevel


def cfg(key):
    return settings.ROADALERT[key]


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bounding_box(lat, lng, radius_m):
    """Cheap lat/lng box used to pre-filter rows before exact distance checks."""
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


@dataclass
class VerificationResult:
    level: int = VerificationLevel.USER_REPORTED
    confidence: int = 0
    evidence: list = field(default_factory=list)
    estimated_delay_min: int = 0
    severity: str = Severity.MODERATE
    distinct_reporters: int = 0
    stuck_users: int = 0


def find_matching_incident(incident_type, lat, lng, now=None):
    """Return an active incident of the same type close to this spot, if any."""
    now = now or timezone.now()
    since = now - timedelta(minutes=cfg("REPORT_WINDOW_MIN"))
    la0, la1, lo0, lo1 = bounding_box(lat, lng, cfg("NEARBY_RADIUS_M"))
    candidates = Incident.objects.filter(
        resolved_at__isnull=True,
        incident_type=incident_type,
        updated_at__gte=since,
        latitude__range=(la0, la1),
        longitude__range=(lo0, lo1),
    )
    best, best_d = None, None
    for inc in candidates:
        d = haversine_m(lat, lng, inc.latitude, inc.longitude)
        if d <= cfg("NEARBY_RADIUS_M") and (best_d is None or d < best_d):
            best, best_d = inc, d
    return best


def _trail_points(incident, window_start):
    """All GPS points near the incident since window_start."""
    la0, la1, lo0, lo1 = bounding_box(incident.latitude, incident.longitude, cfg("GPS_MATCH_RADIUS_M"))
    pts = GpsPoint.objects.filter(
        recorded_at__gte=window_start,
        latitude__range=(la0, la1),
        longitude__range=(lo0, lo1),
    ).select_related("journey")
    return [
        p for p in pts
        if haversine_m(incident.latitude, incident.longitude, p.latitude, p.longitude)
        <= cfg("GPS_MATCH_RADIUS_M")
    ]


def _stuck_minutes_per_journey(points):
    """
    For each journey, the longest stretch (minutes) spent below the congestion
    speed inside the zone. Returns {journey_id: minutes}.
    """
    by_journey = {}
    for p in points:
        if p.speed_kmh < cfg("CONGESTION_SPEED_KMH"):
            by_journey.setdefault(p.journey_id, []).append(p.recorded_at)
    result = {}
    for jid, times in by_journey.items():
        times.sort()
        result[jid] = (times[-1] - times[0]).total_seconds() / 60.0
    return result


def evaluate(incident):
    """Compute verification for an incident from everything we currently know."""
    res = VerificationResult()
    reports = list(incident.reports.select_related("reporter"))
    if not reports:
        # No user reports (e.g. an incident raised by a camera node or entered by staff).
        # Keep what we know: severity stays as is, and an authority confirmation still counts.
        res.severity = incident.severity
        if incident.authority_confirmed:
            res.level = VerificationLevel.AUTHORITY_CONFIRMED
            res.confidence = 100
            res.evidence.append("authority_confirmed")
        return res

    now = timezone.now()
    # People are usually stuck *before* the first report is filed, so the GPS trail
    # window reaches well back from the incident start (a 15-minute window cut a
    # 32-minute jam down to 14 minutes in testing).
    window_start = incident.started_at - timedelta(minutes=cfg("TRAIL_LOOKBACK_MIN"))
    res.distinct_reporters = len({r.reporter_id for r in reports if r.reporter_id})

    # Level 1: someone reported it.
    res.level = VerificationLevel.USER_REPORTED
    res.evidence.append("user_reported")
    score = 30

    points = _trail_points(incident, window_start)
    reporter_ids = {r.reporter_id for r in reports if r.reporter_id}

    # Level 2: a reporter's own GPS trail (from an active/recent journey) was at the spot.
    gps_confirmed = any(p.journey.user_id in reporter_ids for p in points)
    if gps_confirmed:
        res.level = max(res.level, VerificationLevel.GPS_CONFIRMED)
        res.evidence.append("gps_confirmed")
        score += 20

    # Level 3: several different people independently report it.
    if res.distinct_reporters >= cfg("MULTI_REPORT_MIN_USERS"):
        res.level = max(res.level, VerificationLevel.MULTIPLE_USERS)
        res.evidence.append("multiple_users")
        score += 20 + min(10, (res.distinct_reporters - cfg("MULTI_REPORT_MIN_USERS")) * 2)

    # Level 4: GPS speed data from journeys confirms people really are crawling here.
    stuck = _stuck_minutes_per_journey(points)
    confirmed_stuck = {j: m for j, m in stuck.items() if m >= cfg("CONGESTION_MIN_MINUTES")}
    res.stuck_users = len(confirmed_stuck)
    if confirmed_stuck:
        res.level = max(res.level, VerificationLevel.SPEED_CONFIRMED)
        res.evidence.append("speed_confirmed")
        score += 15
        res.estimated_delay_min = int(round(max(confirmed_stuck.values())))

    # Level 5: photo or video attached to any report.
    if any(r.photo or r.video for r in reports):
        res.level = max(res.level, VerificationLevel.MEDIA_EVIDENCE)
        res.evidence.append("media_evidence")
        score += 10

    # Level 6: a traffic authority confirmed it.
    if incident.authority_confirmed:
        res.level = VerificationLevel.AUTHORITY_CONFIRMED
        res.evidence.append("authority_confirmed")
        score = 100

    res.confidence = min(score, 99) if not incident.authority_confirmed else 100

    # Severity from delay; closures are their own state.
    if incident.incident_type == IncidentType.ROAD_CLOSURE:
        res.severity = Severity.CLOSED
    elif res.estimated_delay_min >= 30:
        res.severity = Severity.SEVERE
    elif res.estimated_delay_min >= 15:
        res.severity = Severity.HEAVY
    elif res.estimated_delay_min > 0 or res.level >= VerificationLevel.MULTIPLE_USERS:
        res.severity = Severity.MODERATE
    else:
        res.severity = incident.severity
    return res


def refresh_incident(incident):
    """Re-evaluate and persist the verification fields on the incident."""
    res = evaluate(incident)
    incident.verification_level = res.level
    incident.confidence = res.confidence
    incident.report_count = incident.reports.count()
    incident.severity = res.severity
    if res.estimated_delay_min:
        incident.estimated_delay_min = res.estimated_delay_min
    incident.save()
    _alert(incident)
    return res


def _alert(incident):
    """Warn affected travellers. A failure here must never break report submission."""
    try:
        from notifications.services import dispatch_incident_alerts

        dispatch_incident_alerts(incident)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("incident alert dispatch failed")


def submit_report(*, reporter, incident_type, lat, lng, speed_kmh=None, description="",
                  photo=None, video=None, voice_note=None, road_name=""):
    """
    Entry point used by the API: attach the report to a matching incident (or
    start a new one), then re-run verification.
    """
    now = timezone.now()
    incident = find_matching_incident(incident_type, lat, lng, now)
    if incident is None:
        incident = Incident.objects.create(
            incident_type=incident_type,
            road_name=road_name,
            latitude=lat,
            longitude=lng,
            started_at=now,
        )
    elif road_name and not incident.road_name:
        incident.road_name = road_name

    report = IncidentReport.objects.create(
        incident=incident,
        reporter=reporter,
        incident_type=incident_type,
        latitude=lat,
        longitude=lng,
        speed_kmh=speed_kmh,
        description=description,
        photo=photo,
        video=video,
        voice_note=voice_note,
    )
    result = refresh_incident(incident)
    return incident, report, result
