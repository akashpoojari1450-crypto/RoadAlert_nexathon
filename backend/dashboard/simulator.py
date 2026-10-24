"""
Live simulation: a few "phones" drive a road, run into a jam, report it, and the
system verifies it and warns people. Everything goes through the public HTTP API,
exactly like the future mobile app, so this exercises the real pipeline.

One tick = one simulated minute. GPS points are timestamped on that simulated clock
(ending at "now"), so a 36-tick run produces a realistic 36-minute history in a few
seconds. Needs demo accounts: `python manage.py seed_demo --reset --accounts-only`.
"""
import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# A synthetic road, bending through Thokkotu junction (lat, lng).
ROAD = [(12.6400, 74.8700), (12.8160, 74.8500), (13.0900, 74.7900)]
JAM_AT = 1            # index of the vertex where the jam forms (Thokkotu)
JAM_NAME = "NH-66 - Thokkotu"
JAM_START, JAM_END = 0, 30   # the jam is already forming when the drivers arrive
TOTAL_TICKS = 36
NORMAL_KMH, CRAWL_KMH = 45.0, 2.0
ZONE_HALF_KM = 0.15
PASSWORD = "demo12345"

# name, start offset before the jam (km), reports at tick (None = never)
DRIVERS = [
    ("demo_alice", 3.0, 12),
    ("demo_bob", 2.2, 14),
    ("demo_passenger", 1.4, 16),
    ("demo_carol", 15.0, None),   # drives toward the jam later and gets warned
]


class SimulationError(Exception):
    pass


def _km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Road:
    def __init__(self, points):
        self.points = points
        self.cum = [0.0]
        for a, b in zip(points, points[1:]):
            self.cum.append(self.cum[-1] + _km(a, b))
        self.length = self.cum[-1]

    def at(self, s):
        s = max(0.0, min(self.length, s))
        for i in range(1, len(self.cum)):
            if s <= self.cum[i]:
                f = (s - self.cum[i - 1]) / (self.cum[i] - self.cum[i - 1])
                a, b = self.points[i - 1], self.points[i]
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        return self.points[-1]


class Api:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")

    def call(self, method, path, token=None, body=None, ok=(200, 201, 204)):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Token " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise SimulationError(f"Cannot reach {self.base} ({exc.reason}). Is `python manage.py runserver` running?")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode(errors="replace")[:200]
        if status not in ok:
            raise SimulationError(f"{method} {path} -> {status}: {payload}")
        return payload

    def login(self, username, password):
        """Token, or None if the credentials are wrong. A server that can't be reached still raises."""
        result = self.call("POST", "/api/auth/login/", body={"username": username, "password": password},
                           ok=(200, 400))
        return result.get("token") if isinstance(result, dict) else None


def run(base_url, interval=1.0, employer=("demo_employer", PASSWORD), authority=("demo_authority", PASSWORD),
        log=print):
    """Run the scenario. Returns a summary dict (also used by the tests)."""
    api = Api(base_url)
    road = Road(ROAD)
    jam_s = road.cum[JAM_AT]
    zone_start = jam_s - ZONE_HALF_KM
    summary = {"events": [], "notified": {}, "employer_notes": [], "incident": None, "dashboard": [],
               "incident_ids": set()}

    def say(msg):
        log(msg)
        summary["events"].append(msg)

    # ---- sign in the simulated phones ----
    tokens, journeys, pos = {}, {}, {}
    for name, back, _ in DRIVERS:
        tok = api.login(name, PASSWORD)
        if not tok:
            raise SimulationError(f"Could not sign in as {name}. Run: python manage.py seed_demo --reset --accounts-only")
        tokens[name] = tok
        pos[name] = jam_s - back

    clock0 = datetime.now(timezone.utc) - timedelta(minutes=TOTAL_TICKS - 1)
    geometry = [[lng, lat] for lat, lng in ROAD]
    for name, _, _ in DRIVERS:
        lat, lng = road.at(pos[name])
        j = api.call("POST", "/api/journeys/start/", tokens[name], {
            "kind": "commute", "origin_name": "Talapady", "destination_name": "Mulki",
            "origin_lat": lat, "origin_lng": lng, "dest_lat": ROAD[-1][0], "dest_lng": ROAD[-1][1],
            "route_geometry": geometry,
        })
        journeys[name] = j["id"]
        shared = "visible to employer" if j["tracking_visible_to_employer"] else "private"
        say(f"{name} starts a journey ({shared})")

    incident_id = None
    incident_ids = set()
    confirmed = resolved = False
    authority_tok = api.login(*authority) if authority else None
    employer_tok = api.login(*employer) if employer else None
    if authority and not authority_tok:
        say(f"(authority sign-in as {authority[0]} failed, so confirm/resolve steps are skipped)")
    if employer and not employer_tok:
        say(f"(employer sign-in as {employer[0]} failed, so employer checks are skipped)")

    for tick in range(TOTAL_TICKS):
        jam_active = JAM_START <= tick < JAM_END
        when = (clock0 + timedelta(minutes=tick)).isoformat()
        for name, _, report_tick in DRIVERS:
            s = pos[name]
            in_zone = s >= zone_start and s <= jam_s + ZONE_HALF_KM
            if jam_active and (in_zone or s + NORMAL_KMH / 60 >= zone_start) and s < jam_s + ZONE_HALF_KM:
                if not in_zone:
                    s = zone_start                       # arrives at the back of the queue
                else:
                    s += 0.002                           # crawling
                speed = CRAWL_KMH
            else:
                s += NORMAL_KMH / 60
                speed = NORMAL_KMH
            pos[name] = s
            lat, lng = road.at(s)
            api.call("POST", f"/api/journeys/{journeys[name]}/points/", tokens[name],
                     {"points": [{"latitude": lat, "longitude": lng, "speed_kmh": speed, "recorded_at": when}]})

            if report_tick == tick:
                inc = api.call("POST", "/api/incidents/report/", tokens[name], {
                    "incident_type": "heavy_traffic", "latitude": lat, "longitude": lng,
                    "speed_kmh": speed, "road_name": JAM_NAME,
                    "description": "Stuck, barely moving",
                })
                incident_id = incident_id or inc["id"]   # confirm/resolve act on the first incident
                incident_ids.add(inc["id"])
                say(f"minute {tick:>2}: {name} reports heavy traffic -> "
                    f"Level {inc['verification_level']} ({inc['verification_label']}), "
                    f"confidence {inc['confidence']}%, {inc['reports']} report(s), delay {inc['estimated_delay_min']} min")

        if incident_id and authority_tok and not confirmed and tick == 22:
            r = api.call("POST", f"/api/incidents/{incident_id}/confirm/", authority_tok)
            confirmed = True
            say(f"minute {tick:>2}: traffic authority confirms -> Level {r['verification_level']} "
                f"({r['verification_label']}), confidence {r['confidence']}%")
        if incident_id and authority_tok and not resolved and tick == JAM_END + 2:
            api.call("POST", f"/api/incidents/{incident_id}/resolve/", authority_tok)
            resolved = True
            say(f"minute {tick:>2}: jam cleared, authority marks it resolved")
        if interval:
            time.sleep(interval)

    # ---- what each person ended up seeing ----
    summary["incident_ids"] = incident_ids
    if len(incident_ids) > 1:
        say(f"WARNING: reports were split across {len(incident_ids)} incidents (they should merge into one)")
    if incident_id:
        summary["incident"] = api.call("GET", f"/api/incidents/{incident_id}/", tokens["demo_alice"])
    carol_inbox = api.call("GET", "/api/notifications/", tokens["demo_carol"])
    summary["notified"]["demo_carol"] = carol_inbox
    for n in carol_inbox:
        say(f"demo_carol was warned: \"{n['title']}\" - {n['body']}")
    if not carol_inbox:
        say("demo_carol received no warning (unexpected)")
    if employer_tok:
        summary["employer_notes"] = api.call("GET", "/api/notifications/", employer_tok)
        for n in summary["employer_notes"]:
            say(f"employer was told: {n['body']}")
        summary["dashboard"] = api.call("GET", "/api/employer/dashboard/", employer_tok)
        for r in summary["dashboard"]:
            say(f"dashboard: {r['employee']:<16} {r['status']:<10} {r['delay_min']:>3} min  {r['verification']}")
    return summary
