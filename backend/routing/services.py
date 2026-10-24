"""Combine candidate routes with live incidents to produce alerts and recommendations."""
from django.conf import settings

from incidents.geo import distance_to_route_m, route_bbox
from incidents.models import Incident

from .providers import get_provider


def incidents_on_route(coords, radius_m=None):
    """Active incidents within radius_m of the route, nearest-first."""
    radius_m = radius_m or settings.ROUTING["ROUTE_ALERT_RADIUS_M"]
    la0, la1, lo0, lo1 = route_bbox(coords, pad_m=radius_m)
    found = []
    for inc in Incident.objects.filter(
        resolved_at__isnull=True, latitude__range=(la0, la1), longitude__range=(lo0, lo1)
    ):
        d = distance_to_route_m(inc.latitude, inc.longitude, coords)
        if d <= radius_m:
            found.append((d, inc))
    found.sort(key=lambda x: x[0])
    return [inc for _, inc in found]


def describe_route(index, route):
    incs = incidents_on_route(route.coordinates)
    delay = sum(i.estimated_delay_min for i in incs)
    base_min = route.duration_s / 60.0
    return {
        "index": index,
        "distance_km": round(route.distance_m / 1000, 1),
        "base_duration_min": round(base_min),
        "incident_delay_min": delay,
        "effective_duration_min": round(base_min + delay),
        "incidents": [i.id for i in incs],
        "_incidents": incs,
        "geometry": route.coordinates,
    }


def plan(origin, destination):
    """Analyse every candidate route and recommend the one with the lowest effective ETA."""
    routes = get_provider().routes(origin, destination)
    described = [describe_route(i, r) for i, r in enumerate(routes)]
    best = min(described, key=lambda r: r["effective_duration_min"])
    default = described[0]  # the provider's primary route
    alert = None
    if default["_incidents"]:
        top = max(default["_incidents"], key=lambda i: (i.estimated_delay_min, i.confidence))
        alt_available = best["index"] != default["index"] and (
            best["effective_duration_min"] < default["effective_duration_min"]
        )
        alert = {
            "incident_id": top.id,
            "title": f"{top.get_severity_display()} traffic near {top.road_name or 'your route'}",
            "estimated_delay_min": top.estimated_delay_min,
            "possible_reason": top.get_incident_type_display(),
            "confidence": top.confidence,
            "verification": top.get_verification_level_display(),
            "alternative_available": alt_available,
        }
    return {"routes": described, "recommended": best["index"], "alert": alert}


def avoid(origin, destination, incident):
    """Best route that stays clear of one incident ("Avoid This Route")."""
    radius = settings.ROUTING["AVOID_RADIUS_M"]
    routes = get_provider().routes(origin, destination)
    described = [describe_route(i, r) for i, r in enumerate(routes)]
    clear = [
        d for d, r in zip(described, routes)
        if distance_to_route_m(incident.latitude, incident.longitude, r.coordinates) > radius
    ]
    pool = clear or described
    choice = min(pool, key=lambda r: r["effective_duration_min"])
    return {"route": choice, "avoids_incident": bool(clear), "routes_considered": len(described)}
