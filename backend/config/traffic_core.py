"""
Traffic from RoadAlert users' own GPS speeds.

Pure Python (no Django), so it is easy to test. The route is cut into stretches
of about SEGMENT_M metres. GPS points that lie close to the road line are
assigned to a stretch. A stretch only gets a colour when enough DIFFERENT
journeys were seen on it (privacy and reliability); only the median speed is
reported, never who was there.
"""
import math
from collections import defaultdict
from statistics import median

SEGMENT_M = 400      # judge the road in stretches of about 400 m
MATCH_M = 60         # a GPS point belongs to the road if within 60 m of the line
MIN_JOURNEYS = 2     # at least this many different journeys on a stretch
MIN_POINTS = 3       # and at least this many GPS points
SLOW_KMH = 25        # below this: slow. Below the "heavy" speed: heavy.
CELL_M = 100.0


def _dist_point_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def compute_windows(geometry, points, heavy_kmh=10.0, slow_kmh=SLOW_KMH,
                    segment_m=SEGMENT_M, match_m=MATCH_M,
                    min_journeys=MIN_JOURNEYS, min_points=MIN_POINTS):
    """
    geometry: [[lng, lat], ...]  (the route)
    points:   iterable of (journey_id, lat, lng, speed_kmh)
    returns:  [{"from": vertex_index, "to": vertex_index, "level": "heavy|slow|free",
                "speed_kmh": median_speed, "journeys": n}, ...]
    Stretches without enough data are left out (no data is not "clear").
    """
    if len(geometry) < 2:
        return []
    lng0, lat0 = float(geometry[0][0]), float(geometry[0][1])
    ky = 110540.0
    kx = 111320.0 * math.cos(math.radians(lat0))

    def xy(lat, lng):
        return (lng - lng0) * kx, (lat - lat0) * ky

    pts = [xy(float(g[1]), float(g[0])) for g in geometry]
    n_seg = len(pts) - 1

    # cumulative distance and a stretch number for every road segment
    cum = [0.0]
    for i in range(n_seg):
        cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
    seg_window = [int(cum[i] // segment_m) for i in range(n_seg)]

    # grid so each GPS point only checks nearby road segments
    grid = defaultdict(list)
    for i in range(n_seg):
        (ax, ay), (bx, by) = pts[i], pts[i + 1]
        x0, x1 = min(ax, bx) - match_m, max(ax, bx) + match_m
        y0, y1 = min(ay, by) - match_m, max(ay, by) + match_m
        for cx in range(int(x0 // CELL_M), int(x1 // CELL_M) + 1):
            for cy in range(int(y0 // CELL_M), int(y1 // CELL_M) + 1):
                grid[(cx, cy)].append(i)

    per_window = defaultdict(list)  # window -> [(journey_id, speed)]
    for journey_id, lat, lng, speed in points:
        x, y = xy(float(lat), float(lng))
        best, best_d = None, None
        for i in grid.get((int(x // CELL_M), int(y // CELL_M)), ()):
            d = _dist_point_segment(x, y, *pts[i], *pts[i + 1])
            if d <= match_m and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is not None:
            per_window[seg_window[best]].append((journey_id, float(speed)))

    # first and last vertex of every stretch
    first_vertex, last_vertex = {}, {}
    for i in range(n_seg):
        w = seg_window[i]
        first_vertex.setdefault(w, i)
        last_vertex[w] = i + 1

    out = []
    for w in sorted(per_window):
        samples = per_window[w]
        journeys = {j for j, _ in samples}
        if len(journeys) < min_journeys or len(samples) < min_points:
            continue
        speed = median(s for _, s in samples)
        level = "heavy" if speed < heavy_kmh else "slow" if speed < slow_kmh else "free"
        out.append({
            "from": first_vertex[w],
            "to": last_vertex[w],
            "level": level,
            "speed_kmh": round(speed, 1),
            "journeys": len(journeys),
        })
    return out
