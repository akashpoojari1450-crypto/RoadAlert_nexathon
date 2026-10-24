"""Geometry helpers for routes. Coordinates are GeoJSON order: [lng, lat]."""
import math

M_PER_DEG = 111_320.0


def _to_xy(lat, lng, lat0, lng0):
    """Project to local metres around (lat0, lng0). Accurate enough for distances of a few km."""
    return (lng - lng0) * M_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG


def _point_segment_m(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_route_m(lat, lng, coords):
    """Shortest distance in metres from a point to a route polyline ([[lng, lat], ...])."""
    if not coords:
        return float("inf")
    if len(coords) == 1:
        ax, ay = _to_xy(coords[0][1], coords[0][0], lat, lng)
        return math.hypot(ax, ay)
    best = float("inf")
    prev = _to_xy(coords[0][1], coords[0][0], lat, lng)
    for lng2, lat2 in coords[1:]:
        cur = _to_xy(lat2, lng2, lat, lng)
        best = min(best, _point_segment_m(0.0, 0.0, prev[0], prev[1], cur[0], cur[1]))
        prev = cur
    return best


def route_bbox(coords, pad_m=0.0):
    lats = [c[1] for c in coords]
    lngs = [c[0] for c in coords]
    pad_lat = pad_m / M_PER_DEG
    pad_lng = pad_m / (M_PER_DEG * max(math.cos(math.radians(sum(lats) / len(lats))), 0.01))
    return min(lats) - pad_lat, max(lats) + pad_lat, min(lngs) - pad_lng, max(lngs) + pad_lng


def valid_geometry(coords, max_points=5000):
    """True if coords is a list of [lng, lat] pairs with sane ranges."""
    if not isinstance(coords, list) or not (2 <= len(coords) <= max_points):
        return False
    for c in coords:
        if (not isinstance(c, (list, tuple)) or len(c) < 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in c[:2])
                or not (-180 <= c[0] <= 180 and -90 <= c[1] <= 90)):
            return False
    return True


def route_progress_m(lat, lng, coords):
    """
    How far along the route (metres from its start) the point is, measured at the
    closest spot on the route. Used to tell whether a traveller is before or past an
    incident. Assumes the route does not loop back over itself.
    """
    lat0 = sum(c[1] for c in coords) / len(coords)
    kx = M_PER_DEG * math.cos(math.radians(lat0))

    def xy(la, ln):
        return ln * kx, la * M_PER_DEG

    px, py = xy(lat, lng)
    best_d, best_along, run = float("inf"), 0.0, 0.0
    ax, ay = xy(coords[0][1], coords[0][0])
    for lng2, lat2 in coords[1:]:
        bx, by = xy(lat2, lng2)
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (seg * seg)))
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if d < best_d:
            best_d, best_along = d, run + t * seg
        run += seg
        ax, ay = bx, by
    return best_along
