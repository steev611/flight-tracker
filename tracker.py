"""Flight tracker: poll adsb.lol, detect transitions, log + email."""

import argparse
import datetime
import json
import os
import pathlib
import smtplib
import sys
import time
from email.message import EmailMessage

import requests

from lib import airports
from lib.state_machine import classify_observation, empty_state, step


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
FLIGHTS_DIR = ROOT / "flights"
ADSB_URL = "https://api.adsb.lol/v2/reg/{reg}"
GLOBE_URL = "https://globe.adsb.lol/?icao={icao}"
HTTP_TIMEOUT = 20


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Print actions; don't write state or send email")
    p.add_argument("--no-email", action="store_true",
                   help="Run normally but skip sending email")
    args = p.parse_args(argv)

    config = json.loads(CONFIG_PATH.read_text())
    state = json.loads(STATE_PATH.read_text())
    state.setdefault("aircraft", {})
    state_changed = False
    all_events: list[tuple[dict, list]] = []

    ts = int(time.time())
    for ac in config["aircraft"]:
        reg = ac["registration"]
        icao = ac["icao24"].lower()
        prior = state["aircraft"].get(reg) or empty_state()

        try:
            ac_entry = fetch_adsb_entry(reg)
        except Exception as e:
            print(f"[{reg}] adsb.lol fetch failed: {e}", file=sys.stderr)
            continue

        obs = classify_observation(
            ac_entry, ts=ts,
            airborne_kts=config.get("ground_speed_airborne_knots", 50),
        )
        new_state, events = step(
            prior, obs,
            absence_threshold=config.get("absence_threshold_polls", 3),
        )

        print(f"[{reg}] {prior['status']} -> {new_state['status']}"
              f"  obs={obs['kind']}"
              f"  events={[e.type for e in events]}")

        if new_state != prior:
            state["aircraft"][reg] = new_state
            state_changed = True

        for ev in events:
            record_event(ev, ac, new_state, prior, args.dry_run)
            all_events.append((ac, ev))

    if state_changed and not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")

    if not args.dry_run and not args.no_email:
        for ac, ev in all_events:
            try:
                send_email(ac, ev)
            except Exception as e:
                print(f"email send failed for {ac['registration']} {ev.type}: {e}",
                      file=sys.stderr)
    elif args.dry_run and all_events:
        for ac, ev in all_events:
            subj, body = render_email(ac, ev)
            print(f"\n--- DRY-RUN EMAIL ---\nSubject: {subj}\n{body}\n---")

    return 0


def fetch_adsb_entry(reg: str) -> dict | None:
    r = requests.get(
        ADSB_URL.format(reg=reg),
        headers={"User-Agent": "flight-tracker (github.com)"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    ac_list = data.get("ac") or []
    return ac_list[0] if ac_list else None


def record_event(ev, ac: dict, new_state: dict, prior: dict, dry_run: bool):
    """Append event to events log and, on landing/signal_lost, close the flight record."""
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    month_key = now_dt.strftime("%Y-%m")
    if dry_run:
        return

    FLIGHTS_DIR.mkdir(exist_ok=True)
    events_path = FLIGHTS_DIR / f"events_{month_key}.jsonl"
    record = {
        "ts": int(now_dt.timestamp()),
        "iso_utc": now_dt.isoformat(),
        "registration": ac["registration"],
        "icao24": ac["icao24"],
        "type": ev.type,
        "details": ev.details,
    }
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    if ev.type == "landing":
        # Close out the flight with a summary record.
        flights_path = FLIGHTS_DIR / f"flights_{month_key}.jsonl"
        # Reconstruct takeoff from the landing event's flight_id + prior state's
        # current_flight_id. We didn't persist the takeoff position separately;
        # the takeoff event in events log has it. We just record what we know.
        arrived = ev.details.get("arrived_at") or {}
        dest = airports.describe_position(arrived.get("lat"), arrived.get("lon"))
        summary = {
            "flight_id": ev.details.get("flight_id"),
            "registration": ac["registration"],
            "icao24": ac["icao24"],
            "landed_ts": int(now_dt.timestamp()),
            "landed_iso_utc": now_dt.isoformat(),
            "destination": dest,
            "destination_lat": arrived.get("lat"),
            "destination_lon": arrived.get("lon"),
        }
        with flights_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")


def render_email(ac: dict, ev) -> tuple[str, str]:
    reg = ac["registration"]
    icao = ac["icao24"].lower()
    type_owner = f"{ac.get('type','')} — {ac.get('owner','')}".strip(" —")
    live = GLOBE_URL.format(icao=icao)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if ev.type == "takeoff":
        pos = ev.details.get("position") or {}
        origin = "—"
        if ev.details.get("departed_from"):
            df = ev.details["departed_from"]
            origin = airports.describe_position(df.get("lat"), df.get("lon"))
        elif ev.details.get("prior_status") in ("unknown", "absent"):
            origin = "(unknown — just acquired signal)"
        cur = airports.describe_position(pos.get("lat"), pos.get("lon"), max_nm=20)
        subj = f"[{reg}] Takeoff — {origin}"
        body = (
            f"Aircraft: {reg} ({type_owner})\n"
            f"Status: AIRBORNE  (was {ev.details.get('prior_status')})\n"
            f"Departed from: {origin}\n"
            f"Currently near: {cur}\n"
            f"Altitude: {pos.get('alt')} ft   Speed: {pos.get('gs')} kts\n"
            f"Detected at: {now}\n\n"
            f"Live view: {live}\n"
        )
        return subj, body

    if ev.type == "landing":
        arr = ev.details.get("arrived_at") or {}
        dest = airports.describe_position(arr.get("lat"), arr.get("lon"))
        subj = f"[{reg}] Landed at {dest}"
        body = (
            f"Aircraft: {reg} ({type_owner})\n"
            f"Status: ON GROUND\n"
            f"Arrived at: {dest}\n"
            f"Position: {arr.get('lat')}, {arr.get('lon')}\n"
            f"Detected at: {now}\n\n"
            f"Live view (last trace): {live}\n"
        )
        return subj, body

    if ev.type == "signal_lost":
        lk = ev.details.get("last_known_position") or {}
        where = airports.describe_position(lk.get("lat"), lk.get("lon"), max_nm=30)
        subj = f"[{reg}] Signal lost — last seen near {where}"
        body = (
            f"Aircraft: {reg} ({type_owner})\n"
            f"Status: SIGNAL LOST (was airborne)\n"
            f"Absent for: {ev.details.get('absent_polls')} polls\n"
            f"Last known position: {lk.get('lat')}, {lk.get('lon')} ({where})\n"
            f"Last altitude: {lk.get('alt')} ft   Last speed: {lk.get('gs')} kts\n"
            f"Detected at: {now}\n\n"
            f"This usually means the plane landed outside ADS-B coverage.\n"
            f"Live view: {live}\n"
        )
        return subj, body

    return f"[{reg}] {ev.type}", json.dumps(ev.details, indent=2)


def send_email(ac: dict, ev):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addr = os.environ["NOTIFY_TO"]
    from_addr = os.environ.get("NOTIFY_FROM", user)

    subj, body = render_email(ac, ev)
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    print(f"emailed {to_addr}: {subj}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
