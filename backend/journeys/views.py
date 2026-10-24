from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import TrackingPolicy, User
from accounts.permissions import IsEmployer
from incidents.geo import valid_geometry
from incidents.models import Incident
from incidents.verification import bounding_box, haversine_m

from .models import GpsPoint, Journey


def may_share_with_employer(user, kind):
    """
    Privacy gate, evaluated once when a journey starts.
    Sharing requires ALL of: employee role, a company, the employee's own
    consent, a company policy that covers this kind of journey. Personal
    journeys are never shared. (Working-hours limits apply to official travel;
    commutes happen outside working hours by definition.)
    """
    if user.role != User.Role.EMPLOYEE or not user.company_id or not user.tracking_consent:
        return False
    if kind == Journey.Kind.PERSONAL:
        return False
    policy = TrackingPolicy.objects.filter(company_id=user.company_id).first()
    if policy is None:
        return False
    if policy.scope == TrackingPolicy.Scope.COMMUTE and kind != Journey.Kind.COMMUTE:
        return False
    if policy.scope == TrackingPolicy.Scope.OFFICIAL_TRAVEL and kind != Journey.Kind.OFFICIAL_TRAVEL:
        return False
    if kind == Journey.Kind.OFFICIAL_TRAVEL and policy.working_hours_start and policy.working_hours_end:
        now_t = timezone.localtime().time()
        if not (policy.working_hours_start <= now_t <= policy.working_hours_end):
            return False
    return True


class JourneySerializer(serializers.ModelSerializer):
    tracking_visible_to_employer = serializers.BooleanField(source="shared_with_employer", read_only=True)
    route_geometry = serializers.JSONField(required=False, write_only=True)

    def validate_route_geometry(self, value):
        if not valid_geometry(value):
            raise serializers.ValidationError("Expected a list of [lng, lat] pairs (2 to 5000 points).")
        return value

    class Meta:
        model = Journey
        fields = [
            "id", "kind", "status", "origin_name", "destination_name",
            "origin_lat", "origin_lng", "dest_lat", "dest_lng", "route_geometry",
            "tracking_visible_to_employer", "started_at", "ended_at", "expected_minutes",
        ]
        read_only_fields = ["id", "status", "tracking_visible_to_employer", "started_at", "ended_at"]


class StartJourneyView(APIView):
    def post(self, request):
        s = JourneySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        kind = s.validated_data.get("kind", Journey.Kind.COMMUTE)
        journey = s.save(user=request.user, shared_with_employer=may_share_with_employer(request.user, kind))
        # The employee is told explicitly whether tracking is visible to the employer.
        return Response(JourneySerializer(journey).data, status=status.HTTP_201_CREATED)


class GpsPointInput(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    speed_kmh = serializers.FloatField(min_value=0, default=0)
    recorded_at = serializers.DateTimeField(required=False)


class GpsBatchView(APIView):
    """POST a batch of GPS points (the app buffers points and uploads them together)."""

    def post(self, request, pk):
        journey = get_object_or_404(Journey, pk=pk, user=request.user)
        if journey.status != Journey.Status.ACTIVE:
            return Response({"detail": "Journey has ended."}, status=400)
        s = GpsPointInput(data=request.data.get("points", []), many=True)
        s.is_valid(raise_exception=True)
        if len(s.validated_data) > 500:
            return Response({"detail": "At most 500 points per request."}, status=400)
        now = timezone.now()
        GpsPoint.objects.bulk_create([
            GpsPoint(journey=journey, recorded_at=p.get("recorded_at") or now,
                     latitude=p["latitude"], longitude=p["longitude"], speed_kmh=p["speed_kmh"])
            for p in s.validated_data
        ])
        return Response({"saved": len(s.validated_data)}, status=status.HTTP_201_CREATED)


class EndJourneyView(APIView):
    def post(self, request, pk):
        journey = get_object_or_404(Journey, pk=pk, user=request.user)
        journey.status = Journey.Status.COMPLETED
        journey.ended_at = timezone.now()
        journey.save(update_fields=["status", "ended_at"])
        return Response(JourneySerializer(journey).data)


class MyJourneysView(APIView):
    def get(self, request):
        return Response(JourneySerializer(request.user.journeys.all()[:50], many=True).data)


def _stuck_minutes(points):
    """Longest span (minutes) spent below the congestion speed, from a journey's own points."""
    slow = [p.recorded_at for p in points if p.speed_kmh < settings.ROADALERT["CONGESTION_SPEED_KMH"]]
    if len(slow) < 2:
        return 0.0
    return (max(slow) - min(slow)).total_seconds() / 60.0


def _incident_near(points, journey):
    """
    The closest incident that was live during this journey and lies near where the
    employee was slow (or, if never slow, near the start/end of the trip).
    Incidents resolved before the journey began are history, not an explanation.
    """
    slow = [p for p in points if p.speed_kmh < settings.ROADALERT["CONGESTION_SPEED_KMH"]]
    probes = (slow[:1] + slow[-1:]) or (points[:1] + points[-1:])
    live = Q(resolved_at__isnull=True) | Q(resolved_at__gte=journey.started_at)
    best, best_d = None, None
    for p in probes:
        la0, la1, lo0, lo1 = bounding_box(p.latitude, p.longitude, settings.ROADALERT["GPS_MATCH_RADIUS_M"])
        for inc in Incident.objects.filter(live, latitude__range=(la0, la1), longitude__range=(lo0, lo1)):
            d = haversine_m(p.latitude, p.longitude, inc.latitude, inc.longitude)
            if d <= settings.ROADALERT["GPS_MATCH_RADIUS_M"] and (best_d is None or d < best_d):
                best, best_d = inc, d
    return best


def employer_rows(company, hours=12):
    """Rows for the employer dashboard (API and web page share this). Consent rules are applied here."""
    since = timezone.now() - timedelta(hours=hours)
    journeys = (
        Journey.objects.filter(
            user__company=company,
            shared_with_employer=True,
            user__tracking_consent=True,
            started_at__gte=since,
        ).select_related("user")
    )
    rows = []
    for j in journeys:
        points = list(j.points.all())
        stuck = _stuck_minutes(points)
        inc = _incident_near(points, j)
        is_stuck = stuck >= settings.ROADALERT["CONGESTION_MIN_MINUTES"]
        if is_stuck and inc and inc.incident_type == "road_closure":
            state = "Road Block"
        elif is_stuck:
            state = "Traffic"
        else:
            state = "Normal"
        delay = int(round(stuck)) if state != "Normal" else 0
        # A normal trip is verified by the employee's own GPS only; an incident nearby
        # is not evidence about this employee.
        if state == "Normal":
            verification = "GPS"
        elif inc:
            verification = inc.get_verification_level_display()
        else:
            verification = "GPS speed data"
        photo_url = None
        if inc:
            report_with_photo = inc.reports.exclude(photo="").exclude(photo__isnull=True).first()
            if report_with_photo and report_with_photo.photo:
                photo_url = report_with_photo.photo.url

        rows.append({
            "employee": j.user.username,
            "route": f"{j.origin_name} - {j.destination_name}".strip(" -"),
            "status": state,
            "delay_min": delay,
            "verification": verification,
            "journey_status": j.status,
            "evidence": _evidence_text(j, points, inc, stuck),
            "photo_url": photo_url,
        })
    return rows


class EmployerDashboardView(APIView):
    """
    GET /api/employer/dashboard/ - employees currently (or recently) travelling.
    Only returns journeys the employee consented to share and the policy allowed.
    """

    permission_classes = [IsEmployer]

    def get(self, request):
        hours = min(int(request.query_params.get("hours", 12)), 72)
        return Response(employer_rows(request.user.company, hours))


def _evidence_text(journey, points, incident, stuck):
    if stuck < settings.ROADALERT["CONGESTION_MIN_MINUTES"] or not points:
        return ""
    slow = [p for p in points if p.speed_kmh < settings.ROADALERT["CONGESTION_SPEED_KMH"]]
    t0 = timezone.localtime(min(p.recorded_at for p in slow)).strftime("%I:%M %p")
    t1 = timezone.localtime(max(p.recorded_at for p in slow)).strftime("%I:%M %p")
    place = incident.road_name if incident and incident.road_name else "the route"
    cause = f" ({incident.get_incident_type_display().lower()})" if incident else ""
    return (f"Slow-moving traffic detected near {place}{cause} from {t0} to {t1}. "
            f"Employee GPS shows the employee remained in the affected zone.")
