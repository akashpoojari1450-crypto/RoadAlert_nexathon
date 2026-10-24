import json
import urllib.parse
import urllib.request

from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.views import APIView

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RoadAlertApp/0.1 (student project)"


class PlaceSearchView(APIView):
    """GET /api/places/search/?q=mulki&lat=12.9&lng=74.8 -> [{name, lat, lng}].
    The server asks the search service, so phones do not need to reach it."""

    def get(self, request):
        q = request.query_params.get("q", "").strip()
        if len(q) < 2:
            return Response({"detail": "Type at least 2 characters."}, status=400)
        params = {"q": q, "format": "jsonv2", "limit": "5"}
        lat = lng = None
        try:
            lat = float(request.query_params["lat"])
            lng = float(request.query_params["lng"])
            params["viewbox"] = f"{lng - 1},{lat + 1},{lng + 1},{lat - 1}"
        except (KeyError, ValueError):
            pass
        key = "places:%s:%s:%s" % (q.lower(), round(lat or 0, 1), round(lng or 0, 1))
        cached = cache.get(key)
        if cached is not None:
            return Response(cached)
        url = NOMINATIM + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return Response({"detail": "Place search is unavailable: %s" % exc}, status=502)
        places = [
            {"name": d["display_name"], "lat": float(d["lat"]), "lng": float(d["lon"])}
            for d in data
        ]
        cache.set(key, places, 3600)
        return Response(places)