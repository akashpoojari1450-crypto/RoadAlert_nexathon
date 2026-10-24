from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAuthorityOrAdmin

from .models import Incident, IncidentType
from .reports import monthly_report
from .verification import bounding_box, haversine_m, refresh_incident, submit_report


class IncidentSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_incident_type_display", read_only=True)
    verification_label = serializers.CharField(source="get_verification_level_display", read_only=True)
    reports = serializers.IntegerField(source="report_count", read_only=True)
    photo_url = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    def get_photo_url(self, obj):
        report = obj.reports.exclude(photo="").exclude(photo__isnull=True).first()
        if report and report.photo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(report.photo.url)
            return report.photo.url
        return None

    def get_description(self, obj):
        report = obj.reports.exclude(description="").exclude(description__isnull=True).first()
        return report.description if report else ""

    class Meta:
        model = Incident
        fields = [
            "id", "incident_type", "type_label", "road_name", "latitude", "longitude",
            "severity", "verification_level", "verification_label", "confidence",
            "reports", "estimated_delay_min", "started_at", "resolved_at",
            "photo_url", "description",
        ]


class ReportInputSerializer(serializers.Serializer):
    incident_type = serializers.ChoiceField(choices=IncidentType.choices)
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    speed_kmh = serializers.FloatField(required=False, min_value=0)
    description = serializers.CharField(required=False, allow_blank=True)
    road_name = serializers.CharField(required=False, allow_blank=True)
    photo = serializers.ImageField(required=False)
    video = serializers.FileField(required=False)
    voice_note = serializers.FileField(required=False)


class ReportView(APIView):
    """POST /api/incidents/report/ - the 'Report Road Problem' screen."""

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        s = ReportInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        incident, report, result = submit_report(
            reporter=request.user,
            incident_type=d["incident_type"],
            lat=d["latitude"],
            lng=d["longitude"],
            speed_kmh=d.get("speed_kmh"),
            description=d.get("description", ""),
            photo=d.get("photo"),
            video=d.get("video"),
            voice_note=d.get("voice_note"),
            road_name=d.get("road_name", ""),
        )
        incident.refresh_from_db()
        body = IncidentSerializer(incident).data
        body["evidence"] = result.evidence
        return Response(body, status=status.HTTP_201_CREATED)


class NearbyView(APIView):
    """GET /api/incidents/nearby/?lat=&lng=&radius_m=  - live map data."""

    def get(self, request):
        try:
            lat = float(request.query_params["lat"])
            lng = float(request.query_params["lng"])
            radius = min(float(request.query_params.get("radius_m", 5000)), 50_000)
        except (KeyError, ValueError):
            return Response({"detail": "lat and lng are required numbers."}, status=400)
        la0, la1, lo0, lo1 = bounding_box(lat, lng, radius)
        qs = Incident.objects.filter(
            resolved_at__isnull=True, latitude__range=(la0, la1), longitude__range=(lo0, lo1)
        )
        items = []
        for inc in qs:
            d = haversine_m(lat, lng, inc.latitude, inc.longitude)
            if d <= radius:
                row = IncidentSerializer(inc).data
                row["distance_m"] = round(d)
                items.append(row)
        items.sort(key=lambda r: r["distance_m"])
        return Response(items)


class IncidentDetailView(APIView):
    def get(self, request, pk):
        return Response(IncidentSerializer(get_object_or_404(Incident, pk=pk)).data)


class AuthorityConfirmView(APIView):
    """Level 6: a traffic authority confirms (or later resolves) an incident."""

    permission_classes = [IsAuthorityOrAdmin]

    def post(self, request, pk):
        inc = get_object_or_404(Incident, pk=pk)
        inc.authority_confirmed = True
        inc.save(update_fields=["authority_confirmed"])
        refresh_incident(inc)
        inc.refresh_from_db()
        return Response(IncidentSerializer(inc).data)


class ResolveView(APIView):
    permission_classes = [IsAuthorityOrAdmin]

    def post(self, request, pk):
        inc = get_object_or_404(Incident, pk=pk)
        inc.resolved_at = timezone.now()
        inc.save(update_fields=["resolved_at"])
        return Response(IncidentSerializer(inc).data)


class MonthlyReportView(APIView):
    """GET /api/incidents/reports/monthly/?year=2026&month=9  (authority/admin)."""

    permission_classes = [IsAuthorityOrAdmin]

    def get(self, request):
        now = timezone.now()
        try:
            year = int(request.query_params.get("year", now.year))
            month = int(request.query_params.get("month", now.month))
            return Response(monthly_report(year, month))
        except ValueError:
            return Response({"detail": "Invalid year/month."}, status=400)
