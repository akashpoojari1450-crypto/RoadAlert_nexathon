from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from incidents.geo import valid_geometry
from journeys.models import GpsPoint, Journey

from .traffic_core import MIN_JOURNEYS, compute_windows

WINDOW_MINUTES = 15  # only speeds recorded in the last 15 minutes count


class RouteTrafficView(APIView):
    """
    POST /api/traffic/route/   {"geometry": [[lng, lat], ...]}
    Live traffic along a route, from the GPS speeds of journeys that are
    active right now. Anonymous: only stretch, level and median speed.
    """

    def post(self, request):
        geometry = request.data.get("geometry")
        if not valid_geometry(geometry):
            return Response(
                {"detail": "Expected a list of [lng, lat] pairs (2 to 5000 points)."},
                status=400,
            )
        cfg = getattr(settings, "ROADALERT", {})
        heavy_kmh = float(cfg.get("CONGESTION_SPEED_KMH", 10))

        lngs = [float(g[0]) for g in geometry]
        lats = [float(g[1]) for g in geometry]
        pad = 0.002
        since = timezone.now() - timedelta(minutes=WINDOW_MINUTES)
        rows = (
            GpsPoint.objects.filter(
                journey__status=Journey.Status.ACTIVE,
                recorded_at__gte=since,
                latitude__range=(min(lats) - pad, max(lats) + pad),
                longitude__range=(min(lngs) - pad, max(lngs) + pad),
            ).values_list("journey_id", "latitude", "longitude", "speed_kmh")[:20000]
        )
        windows = compute_windows(geometry, rows, heavy_kmh=heavy_kmh)
        return Response({
            "windows": windows,
            "as_of": timezone.now().isoformat(),
            "window_minutes": WINDOW_MINUTES,
            "min_journeys": MIN_JOURNEYS,
        })
