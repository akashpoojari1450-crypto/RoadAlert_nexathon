"""
Demo traffic for RoadAlert. Run in the backend folder, with the server running:

    python demo_traffic.py start     creates slow/clear GPS data on Mangaluru -> Mulki
    python demo_traffic.py stop      ends the demo journeys (the data disappears)

The data counts for 15 minutes, so run "start" again to refresh it.
It uses three demo employees (demo_bob, demo_carol, demo_dev) through the
normal API, so nothing here touches the database directly.
Demo journeys are named "DEMO TRAFFIC" and may show on the employer page.
"""
import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SERVER = "http://127.0.0.1:8000"
PASSWORD = "demo12345"
USERS = ["demo_bob", "demo_carol", "demo_dev"]
ORIGIN = (12.9141, 74.8560)  # Mangaluru
DEST = (13.0904, 74.7870)    # Mulki
TAG = "DEMO TRAFFIC"
# (start fraction, end fraction, speed km/h) along the route
STRETCHES = [(0.10, 0.25, 45), (0.35, 0.50, 5), (0.50, 0.60, 18), (0.78, 0.92, 50)]


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SERVER + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Token " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit("%s %s failed (%s): %s" % (method, path, e.code, e.read().decode()[:300]))
    except urllib.error.URLError as e:
        raise SystemExit("Cannot reach %s. Is the server running? (%s)" % (SERVER, e))


def login_all():
    tokens = {}
    for name in USERS:
        try:
            req = urllib.request.Request(
                SERVER + "/api/auth/login/",
                data=json.dumps({"username": name, "password": PASSWORD}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                tokens[name] = json.loads(r.read().decode())["token"]
        except urllib.error.HTTPError:
            print("  could not log in as %s (password not %s), skipping" % (name, PASSWORD))
        except urllib.error.URLError as e:
            raise SystemExit("Cannot reach %s. Is the server running? (%s)" % (SERVER, e))
    return tokens


def end_demo_journeys(token):
    ended = 0
    for j in call("GET", "/api/journeys/", token=token) or []:
        if j.get("origin_name") == TAG and j.get("status") == "active":
            call("POST", "/api/journeys/%s/end/" % j["id"], token=token)
            ended += 1
    return ended


def stop():
    tokens = login_all()
    total = sum(end_demo_journeys(t) for t in tokens.values())
    print("Ended %d demo journeys. The demo traffic is gone." % total)


def start():
    tokens = login_all()
    if len(tokens) < 2:
        raise SystemExit("Need at least 2 demo users to log in (traffic needs 2 journeys).")
    first = next(iter(tokens.values()))
    plan = call("POST", "/api/routes/plan/", token=first, body={
        "origin": {"lat": ORIGIN[0], "lng": ORIGIN[1]},
        "destination": {"lat": DEST[0], "lng": DEST[1]}})
    route = plan["routes"][plan.get("recommended", 0)]["geometry"]  # [[lng, lat], ...]
    n = len(route)
    print("Route has %d points." % n)
    random.seed(7)
    now = datetime.now(timezone.utc)
    for name, token in tokens.items():
        end_demo_journeys(token)
        j = call("POST", "/api/journeys/start/", token=token, body={
            "kind": "commute", "origin_name": TAG, "destination_name": TAG,
            "origin_lat": ORIGIN[0], "origin_lng": ORIGIN[1],
            "dest_lat": DEST[0], "dest_lng": DEST[1]})
        points = []
        for a, b, kmh in STRETCHES:
            lo, hi = int(n * a), int(n * b)
            step = max(1, (hi - lo) // 40)
            for i in range(lo, hi, step):
                lng, lat = route[i]
                t = now - timedelta(seconds=random.uniform(0, 480))
                points.append({
                    "latitude": lat + random.uniform(-0.0002, 0.0002),
                    "longitude": lng + random.uniform(-0.0002, 0.0002),
                    "speed_kmh": max(0.0, kmh + random.uniform(-3, 3)),
                    "recorded_at": t.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                })
        for k in range(0, len(points), 400):
            call("POST", "/api/journeys/%s/points/" % j["id"], token=token,
                 body={"points": points[k:k + 400]})
        print("  %s: journey %s with %d points" % (name, j["id"], len(points)))
    print("Done. Open the Map tab and search Mulki within 15 minutes.")
    print("Expected: clear, then heavy (red), slow (orange), clear on the way to Mulki.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("start", "stop"):
        raise SystemExit(__doc__)
    start() if sys.argv[1] == "start" else stop()
