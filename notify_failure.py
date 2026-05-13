"""Send an alert email when track.yml has been failing for N consecutive runs.

Invoked by failure-monitor.yml when the GitHub Actions API reports
N+ consecutive failures of the tracker workflow.
"""

import os
import smtplib
import sys
from email.message import EmailMessage


def main():
    fails = int(os.environ.get("CONSECUTIVE_FAILURES", "0"))
    run_url = os.environ.get("RUN_URL", "")
    workflow = os.environ.get("WORKFLOW", "track")

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    raw_to = os.environ["NOTIFY_TO"]
    recipients = [a.strip() for a in raw_to.split(",") if a.strip()]
    from_addr = os.environ.get("NOTIFY_FROM", user)

    subj = f"[flight-tracker] ALERT — {workflow}.yml has failed {fails} times in a row"
    body = (
        f"The {workflow} workflow has failed {fails} consecutive times.\n\n"
        f"Latest failed run: {run_url}\n\n"
        f"Common causes:\n"
        f"  - Gmail app password expired (rotate at myaccount.google.com/apppasswords)\n"
        f"  - adsb.lol / airplanes.live both down (rare)\n"
        f"  - GitHub Actions outage\n"
        f"  - Bug introduced in a recent commit (check CI test results)\n\n"
        f"Until this is fixed, you will NOT receive takeoff/landing alerts.\n\n"
        f"Repo: https://github.com/steev611/flight-tracker\n"
        f"Actions tab: https://github.com/steev611/flight-tracker/actions\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg, from_addr=from_addr, to_addrs=recipients)
    print(f"failure alert emailed to {len(recipients)} recipient(s)")


if __name__ == "__main__":
    main()
