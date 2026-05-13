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

from lib import airports, email_html, ntfy
from lib.state_machine import classify_observation, empty_state, step
from lib.timefmt import fmt_dual


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
FLIGHTS_DIR = ROOT / "flights"
ADSB_SOURCES = [
    {"name": "adsb.lol",       "url": "https://api.adsb.lol/v2/reg/{reg}"},
    {"name": "airplanes.live", "url": "https://api.airplanes.live/v2/reg/{reg}"},
]
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

        ac_entry, source = fetch_adsb_entry(reg)

        obs = classify_observation(
            ac_entry, ts=ts,
            airborne_kts=config.get("ground_speed_airborne_knots", 50),
        )
        new_state, events = step(
            prior, obs,
            absence_threshold=config.get("absence_threshold_polls", 3),
            inflight_interval_seconds=config.get("inflight_progress_interval_seconds", 1800),
        )

        print(f"[{reg}] {prior['status']} -> {new_state['status']}"
              f"  obs={obs['kind']}  source={source or 'none'}"
              f"  events={[e.type for e in events]}")

        if new_state != prior:
            state["aircraft"][reg] = new_state
            state_changed = True

        for ev in events:
            record_event(ev, ac, new_state, prior, args.dry_run)
            all_events.append((ac, ev))

    if state_changed and not args.dry_run:
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")

    tz_name = config.get("display_timezone", "Europe/London")
    if not args.dry_run and not args.no_email:
        for ac, ev in all_events:
            try:
                send_email(ac, ev, tz_name=tz_name)
            except Exception as e:
                print(f"email send failed for {ac['registration']} {ev.type}: {e}",
                      file=sys.stderr)
            try:
                send_push(ac, ev, tz_name=tz_name)
            except Exception as e:
                print(f"ntfy push failed for {ac['registration']} {ev.type}: {e}",
                      file=sys.stderr)
    elif args.dry_run and all_events:
        for ac, ev in all_events:
            subj, body, _html = render_email(ac, ev, tz_name=tz_name)
            print(f"\n--- DRY-RUN EMAIL ---\nSubject: {subj}\n{body}\n---")

    return 0


def fetch_adsb_entry(reg: str) -> tuple[dict | None, str | None]:
    """Try each ADS-B source in order. Return (entry, source_name).

    Both sources use the same `{"ac": [...]}` schema. We declare the plane
    absent only if ALL sources return empty (or fail). A failure on one
    source falls through to the next — we don't raise.
    """
    for src in ADSB_SOURCES:
        try:
            r = requests.get(
                src["url"].format(reg=reg),
                headers={"User-Agent": "flight-tracker (github.com/steev611/flight-tracker)"},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            ac_list = r.json().get("ac") or []
            if ac_list:
                return ac_list[0], src["name"]
        except Exception as e:
            print(f"  source {src['name']} failed: {e}", file=sys.stderr)
    return None, None


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
        # Close out the flight with a summary record + full position track.
        flights_path = FLIGHTS_DIR / f"flights_{month_key}.jsonl"
        arrived = ev.details.get("arrived_at") or {}
        dest = airports.describe_position(arrived.get("lat"), arrived.get("lon"))
        track = ev.details.get("track") or []
        origin = None
        if track:
            origin = airports.describe_position(track[0].get("lat"), track[0].get("lon"))
        summary = {
            "flight_id": ev.details.get("flight_id"),
            "registration": ac["registration"],
            "icao24": ac["icao24"],
            "takeoff_ts": ev.details.get("takeoff_ts"),
            "landed_ts": int(now_dt.timestamp()),
            "landed_iso_utc": now_dt.isoformat(),
            "elapsed_seconds": ev.details.get("elapsed_seconds"),
            "origin": origin,
            "origin_lat": track[0].get("lat") if track else None,
            "origin_lon": track[0].get("lon") if track else None,
            "destination": dest,
            "destination_lat": arrived.get("lat"),
            "destination_lon": arrived.get("lon"),
            # Compact track: just [lat, lon, alt, ts] per point to keep file small.
            "track": [[p.get("lat"), p.get("lon"), p.get("alt"), p.get("ts")] for p in track],
        }
        with flights_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")


def render_email(ac: dict, ev, tz_name: str = "Europe/London") -> tuple[str, str, str | None]:
    """Return (subject, plain_text_body, html_body | None)."""
    reg = ac["registration"]
    icao = ac["icao24"].lower()
    type_owner = f"{ac.get('type','')} — {ac.get('owner','')}".strip(" —")
    live = GLOBE_URL.format(icao=icao)
    now = fmt_dual(datetime.datetime.now(datetime.timezone.utc), tz_name)
    photo = email_html.lookup_photo(reg)

    def _html(banner_subtitle: str, rows: list[tuple[str, str]]) -> str:
        return email_html.render(
            event_type=ev.type,
            reg=reg,
            aircraft_summary=type_owner,
            body_rows=rows,
            summary_subtitle=banner_subtitle,
            live_url=live,
            detected_at=now,
            photo_url=photo,
        )

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
        html_body = _html(f"Departed from {origin}", [
            ("Currently near", cur),
            ("Altitude",       f"{pos.get('alt')} ft"),
            ("Ground speed",   f"{pos.get('gs')} kts"),
            ("Was",            str(ev.details.get('prior_status'))),
        ])
        return subj, body, html_body

    if ev.type == "landing":
        arr = ev.details.get("arrived_at") or {}
        dest = airports.describe_position(arr.get("lat"), arr.get("lon"))
        elapsed = ev.details.get("elapsed_seconds")
        duration_str = _fmt_duration(elapsed) if elapsed else "unknown"
        subj = f"[{reg}] Landed at {dest}"
        body = (
            f"Aircraft: {reg} ({type_owner})\n"
            f"Status: ON GROUND\n"
            f"Arrived at: {dest}\n"
            f"Position: {arr.get('lat')}, {arr.get('lon')}\n"
            f"Flight duration: {duration_str}\n"
            f"Detected at: {now}\n\n"
            f"Live view (last trace): {live}\n"
        )
        html_body = _html(f"Arrived at {dest}", [
            ("Position",         f"{arr.get('lat')}, {arr.get('lon')}"),
            ("Flight duration",  duration_str),
        ])
        return subj, body, html_body

    if ev.type == "in_flight_progress":
        pos = ev.details.get("position") or {}
        cur = airports.describe_position(pos.get("lat"), pos.get("lon"), max_nm=30)
        elapsed = ev.details.get("elapsed_seconds")
        elapsed_str = _fmt_duration(elapsed) if elapsed else "?"
        subj = f"[{reg}] In flight — near {cur} ({elapsed_str} elapsed)"
        body = (
            f"Aircraft: {reg} ({type_owner})\n"
            f"Status: AIRBORNE — in-flight progress update\n"
            f"Currently near: {cur}\n"
            f"Position: {pos.get('lat')}, {pos.get('lon')}\n"
            f"Altitude: {pos.get('alt')} ft   Speed: {pos.get('gs')} kts\n"
            f"Time since takeoff: {elapsed_str}\n"
            f"Detected at: {now}\n\n"
            f"Live view: {live}\n"
        )
        html_body = _html(f"In flight near {cur} — {elapsed_str} elapsed", [
            ("Position",         f"{pos.get('lat')}, {pos.get('lon')}"),
            ("Altitude",         f"{pos.get('alt')} ft"),
            ("Ground speed",     f"{pos.get('gs')} kts"),
            ("Time since takeoff", elapsed_str),
        ])
        return subj, body, html_body

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
        html_body = _html(f"Last seen near {where}", [
            ("Last position", f"{lk.get('lat')}, {lk.get('lon')}"),
            ("Last altitude", f"{lk.get('alt')} ft"),
            ("Last speed",    f"{lk.get('gs')} kts"),
            ("Absent polls",  str(ev.details.get('absent_polls'))),
            ("Note",          "Usually means landed outside ADS-B coverage."),
        ])
        return subj, body, html_body

    if ev.type == "emergency_squawk":
        squawk = ev.details.get("squawk")
        meaning = ev.details.get("meaning", "unknown")
        pos = ev.details.get("position") or {}
        where = airports.describe_position(pos.get("lat"), pos.get("lon"), max_nm=30) \
            if pos.get("lat") is not None else "position unknown"
        subj = f"[{reg}] EMERGENCY SQUAWK {squawk} — {meaning}"
        body = (
            f"*** EMERGENCY TRANSPONDER CODE DETECTED ***\n\n"
            f"Aircraft: {reg} ({type_owner})\n"
            f"Squawk: {squawk} — {meaning}\n"
            f"Position: {where} ({pos.get('lat')}, {pos.get('lon')})\n"
            f"Altitude: {pos.get('alt')} ft   Speed: {pos.get('gs')} kts\n"
            f"Detected at: {now}\n\n"
            f"Reference:\n"
            f"  7500 = Hijacking / unlawful interference\n"
            f"  7600 = Lost radio communications\n"
            f"  7700 = General emergency\n\n"
            f"This alert fires once per code change. Live: {live}\n"
        )
        html_body = _html(f"Squawk {squawk} — {meaning}", [
            ("Squawk",   f"{squawk} — {meaning}"),
            ("Position", where),
            ("Altitude", f"{pos.get('alt')} ft"),
            ("Speed",    f"{pos.get('gs')} kts"),
        ])
        return subj, body, html_body

    return f"[{reg}] {ev.type}", json.dumps(ev.details, indent=2), None


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def send_email(ac: dict, ev, tz_name: str = "Europe/London"):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    raw_to = os.environ["NOTIFY_TO"]
    recipients = [a.strip() for a in raw_to.split(",") if a.strip()]
    from_addr = os.environ.get("NOTIFY_FROM", user)

    subj, body, html_body = render_email(ac, ev, tz_name=tz_name)
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg, from_addr=from_addr, to_addrs=recipients)
    print(f"emailed {len(recipients)} recipient(s): {subj}")


def send_push(ac: dict, ev, tz_name: str = "Europe/London"):
    """Send a parallel ntfy.sh push notification. No-op if NTFY_TOPIC not set."""
    subj, body, _ = render_email(ac, ev, tz_name=tz_name)
    icao = ac["icao24"].lower()
    ntfy.push_for_event(
        event_type=ev.type,
        title=subj,
        body=body,
        click_url=GLOBE_URL.format(icao=icao),
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
