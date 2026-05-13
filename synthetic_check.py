"""Daily silent health check.

Runs the most failure-prone parts of the pipeline WITHOUT generating any
user-visible notification when everything is healthy. If anything fails,
sends one alert email so the broken state isn't a silent void.

Things tested:
  1. SMTP login (Gmail app passwords are the #1 silent-rot risk — they
     expire if unused for too long, and Google can also revoke them).
  2. adsb.lol API reachable (the data feed we depend on).
  3. airplanes.live API reachable (the fallback feed).

Things NOT tested:
  - ntfy (would send a notification = not silent).
  - OpenSky (only used in manual backfill, not the live path).
"""

import os
import smtplib
import sys
from email.message import EmailMessage

import requests


ADSB_LOL = "https://api.adsb.lol/v2/reg/N83TY"
AIRPLANES_LIVE = "https://api.airplanes.live/v2/reg/N83TY"


def main() -> int:
    failures: list[str] = []

    # 1. SMTP login
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"],
                          int(os.environ.get("SMTP_PORT", "587")),
                          timeout=20) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        print("OK   SMTP login")
    except Exception as e:
        msg = f"SMTP login FAILED: {type(e).__name__}: {e}"
        print(msg, file=sys.stderr)
        failures.append(msg)

    # 2. adsb.lol reachable
    failures.extend(_check_url("adsb.lol", ADSB_LOL))

    # 3. airplanes.live reachable
    failures.extend(_check_url("airplanes.live", AIRPLANES_LIVE))

    if not failures:
        print("\nAll checks passed. Silent — no notification sent.")
        return 0

    # Something broke. Email an alert.
    print(f"\n{len(failures)} check(s) failed. Sending alert email.")
    try:
        send_alert(failures)
    except Exception as e:
        print(f"ALERT EMAIL ALSO FAILED: {e}", file=sys.stderr)
        return 2
    return 1


def _check_url(label: str, url: str) -> list[str]:
    try:
        r = requests.get(url, headers={"User-Agent": "flight-tracker-synthetic"},
                         timeout=15)
        if r.status_code >= 500:
            return [f"{label} HTTP {r.status_code}"]
        # 200 with empty ac:[] is fine — we just want to verify reachability.
        print(f"OK   {label} ({r.status_code})")
        return []
    except Exception as e:
        return [f"{label} unreachable: {type(e).__name__}: {e}"]


def send_alert(failures: list[str]) -> None:
    raw_to = os.environ["NOTIFY_TO"]
    recipients = [a.strip() for a in raw_to.split(",") if a.strip()]
    from_addr = os.environ.get("NOTIFY_FROM", os.environ["SMTP_USER"])

    msg = EmailMessage()
    msg["Subject"] = "[flight-tracker] Daily check FAILED"
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    body = (
        "The daily synthetic check found problems with the flight-tracker pipeline.\n\n"
        "Failures:\n  " + "\n  ".join(f"- {f}" for f in failures) + "\n\n"
        "Common causes:\n"
        "  - SMTP auth failure -> Gmail app password expired or revoked.\n"
        "    Re-generate at https://myaccount.google.com/apppasswords and update\n"
        "    the SMTP_PASS secret in the repo.\n"
        "  - adsb.lol / airplanes.live failure -> Source feed is down. Usually transient.\n"
        "    If both sources are down for > a few hours, takeoff/landing detection won't work.\n\n"
        "Until this is fixed, you will NOT receive event alerts.\n\n"
        "Repo: https://github.com/steev611/flight-tracker\n"
        "Actions: https://github.com/steev611/flight-tracker/actions\n"
    )
    msg.set_content(body)
    with smtplib.SMTP(os.environ["SMTP_HOST"],
                      int(os.environ.get("SMTP_PORT", "587")),
                      timeout=30) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg, from_addr=from_addr, to_addrs=recipients)
    print(f"alert emailed to {len(recipients)} recipient(s)")


if __name__ == "__main__":
    sys.exit(main())
