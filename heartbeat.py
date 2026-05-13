"""Weekly heartbeat: send a 'tracker is alive' email with a 7-day summary.

If this email arrives, the workflow infra is healthy. If it doesn't arrive
Monday morning, something is broken.
"""

import datetime
import glob
import json
import os
import pathlib
import smtplib
import sys
from email.message import EmailMessage

from lib import airports


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
FLIGHTS_DIR = ROOT / "flights"
REPO_URL = "https://github.com/steev611/flight-tracker"


def main():
    config = json.loads(CONFIG_PATH.read_text())
    state = json.loads(STATE_PATH.read_text())
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = int((now - datetime.timedelta(days=7)).timestamp())

    events = collect_recent_events(cutoff)
    body = build_body(config, state, events, now)
    subj = f"[flight-tracker] Weekly heartbeat — {now.strftime('%Y-%m-%d')}"
    send_email(subj, body)
    print(f"heartbeat sent: {subj}")


def collect_recent_events(cutoff_ts: int) -> list[dict]:
    out = []
    if not FLIGHTS_DIR.exists():
        return out
    for path in sorted(FLIGHTS_DIR.glob("events_*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ts", 0) >= cutoff_ts:
                    out.append(rec)
    out.sort(key=lambda r: r["ts"])
    return out


def build_body(config: dict, state: dict, events: list[dict], now: datetime.datetime) -> str:
    lines = [
        "Flight tracker heartbeat.",
        f"As of {now.strftime('%Y-%m-%d %H:%M UTC')} the tracker workflow is running.",
        "",
        "Watched aircraft:",
    ]
    for ac in config["aircraft"]:
        reg = ac["registration"]
        s = state.get("aircraft", {}).get(reg) or {}
        status = s.get("status", "unknown")
        pos = s.get("last_position") or {}
        place = airports.describe_position(pos.get("lat"), pos.get("lon"), max_nm=15) \
            if pos.get("lat") is not None else "no position recorded"
        lines.append(f"  - {reg} ({ac.get('type','')}): {status} — last seen {place}")

    lines += ["", "Events in the last 7 days:"]
    if not events:
        lines.append("  (no takeoff/landing/in-flight events)")
    else:
        for ev in events:
            t = datetime.datetime.fromtimestamp(ev["ts"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            reg = ev.get("registration", "?")
            etype = ev.get("type", "?")
            extra = _event_extra(ev)
            lines.append(f"  - {t}  [{reg}]  {etype}{extra}")

    lines += [
        "",
        f"Repo: {REPO_URL}",
        "If you stop receiving these on Mondays, check the GitHub Actions tab.",
        "",
        "You can stop the heartbeat by disabling the 'heartbeat' workflow in GitHub Actions.",
    ]
    return "\n".join(lines)


def _event_extra(ev: dict) -> str:
    det = ev.get("details", {})
    if ev["type"] == "landing":
        a = det.get("arrived_at") or {}
        place = airports.describe_position(a.get("lat"), a.get("lon"))
        return f" at {place}"
    if ev["type"] == "takeoff":
        df = det.get("departed_from")
        if df:
            place = airports.describe_position(df.get("lat"), df.get("lon"))
            return f" from {place}"
        return ""
    if ev["type"] == "in_flight_progress":
        p = det.get("position") or {}
        return f" near {airports.describe_position(p.get('lat'), p.get('lon'), max_nm=30)}"
    return ""


def send_email(subj: str, body: str):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    raw_to = os.environ["NOTIFY_TO"]
    recipients = [a.strip() for a in raw_to.split(",") if a.strip()]
    from_addr = os.environ.get("NOTIFY_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg, from_addr=from_addr, to_addrs=recipients)


if __name__ == "__main__":
    main()
