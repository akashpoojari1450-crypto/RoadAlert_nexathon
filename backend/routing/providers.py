"""
Routing providers. A provider returns candidate routes between two points.

  OSRMProvider  - real routing via an OSRM server (self-host for production; the
                  public demo server is for light testing only).
  FakeProvider  - deterministic offline routes (a direct line plus a detour), used
                  for development and tests.
"""
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string


class RoutingError(Exception):
    pass


@dataclass
class Route:
    coordinates: list  # [[lng, lat], ...]
    duration_s: float
    distance_m: float


class OSRMProvider:
    def __init__(self, base_url=None, timeout=8):
        self.base_url = (base_url or settings.ROUTING["OSRM_URL"]).rstrip("/")
        self.timeout = timeout

    def routes(self, origin, destination):
        path = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
        qs = urllib.parse.urlencode({"overview": "full", "geometries": "geojson", "alternatives": "true"})
        url = f"{self.base_url}/route/v1/driving/{path}?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise RoutingError(f"Routing service unavailable: {exc}") from exc
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RoutingError(f"No route found ({data.get('code')}).")
        return [
            Route(r["geometry"]["coordinates"], float(r["duration"]), float(r["distance"]))
            for r in data["routes"]
        ]


class FakeProvider:
    """Straight-line route plus one detour bulging ~2 km to the side. No network needed."""

    POINTS = 24
    AVERAGE_KMH = 40.0

    def routes(self, origin, destination):
        (la1, ln1), (la2, ln2) = origin, destination
        straight = math.hypot((la2 - la1) * 111_320, (ln2 - ln1) * 111_320 * math.cos(math.radians(la1)))
        if straight < 1:
            raise RoutingError("Origin and destination are the same place.")
        # unit vector perpendicular to travel direction, in degrees
        dlat, dlng = la2 - la1, ln2 - ln1
        norm = math.hypot(dlat, dlng)
        perp_lat, perp_lng = -dlng / norm, dlat / norm
        bulge = 0.02  # ~2 km

        def build(offset):
            pts = []
            for i in range(self.POINTS + 1):
                t = i / self.POINTS
                w = math.sin(math.pi * t) * offset
                pts.append([ln1 + dlng * t + perp_lng * w, la1 + dlat * t + perp_lat * w])
            return pts

        direct = build(0.0)
        detour = build(bulge)
        detour_len = straight * 1.2
        speed = self.AVERAGE_KMH / 3.6
        return [
            Route(direct, straight / speed, straight),
            Route(detour, detour_len / speed, detour_len),
        ]


def get_provider():
    return import_string(settings.ROUTING["PROVIDER"])()
