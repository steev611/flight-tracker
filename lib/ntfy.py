"""ntfy.sh push notifications — runs in parallel to email.

The topic is whatever you subscribe to in the ntfy mobile app.
Choose a hard-to-guess string so others can't see your notifications.
Set the NTFY_TOPIC env var (or GHA secret) to the topic name.
"""

import os
import requests


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


def push_for_event(event_type: str, title: str, body: str) -> None:
    """Send a push notification keyed by event_type. No-op if NTFY_TOPIC not set.

    Notification has no Click target — tapping it just opens the ntfy app and
    shows the full body. The body itself contains a globe.adsb.lol link the
    user can copy if they want live tracking.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        # HTTP headers are latin-1, so fold any wide unicode in the title to ASCII.
        "Title": _ascii_safe(title),
        "Priority": PRIORITY_BY_EVENT.get(event_type, "default"),
        "Tags": TAGS_BY_EVENT.get(event_type, "airplane"),
    }
    try:
        r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"ntfy push failed: {e}")


# Common typographic characters that don't fit in latin-1 (so they'd
# break HTTP header encoding). Folded to ASCII equivalents.
_ASCII_FOLD = {
    "—": "-",   # em-dash
    "–": "-",   # en-dash
    "‘": "'", "’": "'",   # smart single quotes
    "“": '"', "”": '"',   # smart double quotes
    "…": "...",                 # ellipsis
    "→": "->",                  # right arrow
    " ": " ",                   # non-breaking space
}


def _ascii_safe(s: str) -> str:
    for k, v in _ASCII_FOLD.items():
        s = s.replace(k, v)
    # Anything remaining that latin-1 can't encode, drop entirely.
    return s.encode("latin-1", "replace").decode("latin-1")
