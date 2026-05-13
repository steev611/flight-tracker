"""ntfy.sh push notifications — runs in parallel to email.

The topic is whatever you subscribe to in the ntfy mobile app.
Choose a hard-to-guess string so others can't see your notifications.
Set the NTFY_TOPIC env var (or GHA secret) to the topic name.
"""

import os
import requests
from typing import Optional


PRIORITY_BY_EVENT = {
    "takeoff":            "default",
    "landing":            "default",
    "in_flight_progress": "low",
    "signal_lost":        "high",
    "emergency_squawk":   "urgent",
}

TAGS_BY_EVENT = {
    "takeoff":            "airplane,green_circle",
    "landing":            "airplane,blue_circle",
    "in_flight_progress": "airplane,yellow_circle",
    "signal_lost":        "warning",
    "emergency_squawk":   "rotating_light,rotating_light",
}


def push_for_event(event_type: str, title: str, body: str,
                   click_url: Optional[str] = None) -> None:
    """Send a push notification keyed by event_type. No-op if NTFY_TOPIC not set."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Priority": PRIORITY_BY_EVENT.get(event_type, "default"),
        "Tags": TAGS_BY_EVENT.get(event_type, "airplane"),
    }
    if click_url:
        headers["Click"] = click_url
    try:
        r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"ntfy push failed: {e}")
