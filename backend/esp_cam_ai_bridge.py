"""
RoadAlert ESP32-CAM AI Vision & SOS Call Bridge
------------------------------------------------
Connects your trained ESP32-CAM (running at http://10.43.195.75/) with RoadAlert.
When the AI detects:
  - "accident"  -> Triggers immediate Level 5 SOS Voice Call via Twilio!
  - "ambulance" -> Broadcasts emergency corridor alert (clear the road).
  - "vip"       -> Logs VIP movement and route advisory.

Usage:
  # Test the Accident SOS call immediately:
  python esp_cam_ai_bridge.py --test accident

  # Test Ambulance detection:
  python esp_cam_ai_bridge.py --test ambulance

  # Run live camera stream polling/monitoring:
  python esp_cam_ai_bridge.py --cam http://10.43.195.75/
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DJANGO_SERVER = "http://127.0.0.1:8000"
DEFAULT_CAM_URL = "http://10.43.195.75/"
ROAD_NAME = "NH-66 - Surathkal Junction"


def send_camera_event(detected_class, confidence=0.92, camera_ip="10.43.195.75", road_name=ROAD_NAME):
    endpoint = f"{DJANGO_SERVER}/api/alerts/camera-event/"
    payload = {
        "detected_class": detected_class,
        "confidence": confidence,
        "camera_ip": camera_ip,
        "road_name": road_name,
        "latitude": 12.9850,
        "longitude": 74.8020,
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n[ESP32-CAM] Sending AI detection: '{detected_class.upper()}' (Confidence: {int(confidence*100)}%) -> {endpoint}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[SUCCESS] Response ({resp.status}):")
            print(json.dumps(data, indent=2))
            if detected_class == "accident":
                print("\n*** [SOS TRIGGERED] An automated voice call alert has been queued to Emergency Services! ***")
            return data
    except urllib.error.HTTPError as e:
        print(f"[HTTP Error] {e.code}: {e.read().decode('utf-8')[:300]}")
    except urllib.error.URLError as e:
        print(f"[Connection Error] Cannot reach Django server at {DJANGO_SERVER}. Is it running? ({e})")


def monitor_live_camera(cam_url):
    print(f"Connecting to ESP32-CAM at: {cam_url} ...")
    print("Watching for AI vision classifications (accident, ambulance, vip)...")
    print("Press Ctrl+C to stop.\n")

    # Clean IP address
    cam_ip = cam_url.replace("http://", "").replace("https://", "").split("/")[0]

    status_endpoints = [
        f"{cam_url.rstrip('/')}/detection",
        f"{cam_url.rstrip('/')}/status",
        f"{cam_url.rstrip('/')}/result",
    ]

    working_endpoint = None
    for ep in status_endpoints:
        try:
            req = urllib.request.Request(ep, headers={"User-Agent": "RoadAlertBridge/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                working_endpoint = ep
                print(f" Connected to telemetry endpoint: {working_endpoint}")
                break
        except Exception:
            continue

    if not working_endpoint:
        print(f"[NOTE] Could not find automatic JSON status on {cam_url}.")
        print("To trigger an event from the ESP32 code, have it send an HTTP POST directly to:")
        print(f"  POST {DJANGO_SERVER}/api/alerts/camera-event/ with {{\"detected_class\": \"accident\"}}")
        print("\nOr test right now using: python esp_cam_ai_bridge.py --test accident")
        return

    cooldown_until = 0

    while True:
        try:
            req = urllib.request.Request(working_endpoint, headers={"User-Agent": "RoadAlertBridge/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode("utf-8"))
                detected = str(res.get("class", res.get("detected", ""))).lower().strip()
                conf = float(res.get("confidence", res.get("score", 0.85)))

                if detected in ["accident", "ambulance", "vip"] and time.time() > cooldown_until:
                    send_camera_event(detected, confidence=conf, camera_ip=cam_ip)
                    cooldown_until = time.time() + 60
        except Exception:
            time.sleep(2)
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RoadAlert ESP32-CAM AI Vision & SOS Call Bridge")
    parser.add_argument("--test", choices=["accident", "ambulance", "vip"], help="Simulate an instant AI vision detection")
    parser.add_argument("--cam", default=DEFAULT_CAM_URL, help="ESP32-CAM URL (default: http://10.43.195.75/)")
    parser.add_argument("--road", default=ROAD_NAME, help="Road / corridor name")
    args = parser.parse_args()

    if args.test:
        send_camera_event(args.test, confidence=0.95, road_name=args.road)
    else:
        monitor_live_camera(args.cam)
