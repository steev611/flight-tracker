# flight-tracker

Polls **adsb.lol** + **airplanes.live** for one or more aircraft and emails you
(plus optional phone push) on takeoff / landing / signal loss / in-flight
progress / emergency squawks. Runs as a GitHub Actions cron, free, no servers.
Includes a 30-day OpenSky history backfill and an auto-rebuilt dashboard.

Works for ADS-B Exchange–visible aircraft that mainstream trackers hide
(e.g. LADD/PIA-blocked tails) because the source networks publish unfiltered data.

**Live dashboard:** https://steev611.github.io/flight-tracker/

---

## Notifications

Per event the system sends:
- **Email** (HTML + plain): to every address in `NOTIFY_TO`, banner colored by
  event type, includes a plane photo, summary table, and a "Track live" button.
- **ntfy.sh push** (optional): if `NTFY_TOPIC` is set, parallel phone push goes
  to whoever subscribes to that topic. Per-event priority:
  takeoff/landing=default, in-flight=low, signal-lost=high, emergency=urgent.

Event types:

| Event | Fires when |
|---|---|
| **takeoff** | plane goes airborne (ground or absent → airborne) |
| **in_flight_progress** | every 30 min while airborne |
| **landing** | plane back on ground (airborne → ground) |
| **signal_lost** | 3 consecutive polls absent while airborne |
| **emergency_squawk** | transponder changes to 7500 / 7600 / 7700 |

## Setup

### 1. Tails to watch — `config.json`

```json
{
  "aircraft": [
    { "registration": "N83TY", "icao24": "ab57c1",
      "type": "Falcon 50EX", "owner": "Zoe Air Delaware Inc" }
  ],
  "absence_threshold_polls": 3,
  "ground_speed_airborne_knots": 50,
  "inflight_progress_interval_seconds": 1800,
  "display_timezone": "Europe/London"
}
```

Look up `icao24` (Mode-S hex) at `https://api.adsbdb.com/v0/aircraft/<TAIL>`.

### 2. Repository secrets

`Settings → Secrets and variables → Actions → New repository secret`:

| Name | Required | Example |
|---|---|---|
| `SMTP_HOST` | yes | `smtp.gmail.com` |
| `SMTP_PORT` | yes | `587` |
| `SMTP_USER` | yes | sender Gmail address |
| `SMTP_PASS` | yes | 16-char Gmail [app password](https://myaccount.google.com/apppasswords) (2FA required) |
| `NOTIFY_TO` | yes | comma-separated recipient list |
| `NOTIFY_FROM` | optional | defaults to `SMTP_USER` |
| `OPENSKY_CLIENT_ID` | for backfill | `you@example.com-api-client` |
| `OPENSKY_CLIENT_SECRET` | for backfill | from opensky-network.org/my-opensky/account |
| `NTFY_TOPIC` | optional | hard-to-guess topic name; subscribe in [ntfy app](https://ntfy.sh/app) |

### 3. Backfill (optional, one-time)

`Actions → backfill → Run workflow`. Pulls 7 days from OpenSky and writes
`flights/backfill_<TAIL>.jsonl`.

Note: OpenSky's auth server times out connections from GitHub Actions runners
(IP-range filtering on their end). The workflow has retries but if it fails,
just run `python backfill.py` locally — that path works.

### 4. The tracker runs itself

- `track.yml` runs every 10 min via cron.
- `heartbeat.yml` sends a "tracker alive" summary every Monday 08:00 UTC.
- `keepalive.yml` bumps a heartbeat file weekly so GitHub doesn't auto-pause the cron.
- `failure-monitor.yml` fires on tracker failure and emails an alert if the last 3 runs all failed.
- `test.yml` runs pytest on every push and PR.

## Local development

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                    # 40+ tests
python tracker.py --dry-run                   # poll once, print, don't write/email
python build_dashboard.py                     # rebuild docs/index.html
OPENSKY_CLIENT_ID=… OPENSKY_CLIENT_SECRET=… python backfill.py
```

## Layout

```
tracker.py              poll loop → state machine → email + ntfy → log → dashboard
backfill.py             one-shot OpenSky history pull
heartbeat.py            weekly alive email
build_dashboard.py      regenerates docs/index.html (map + tables)
notify_failure.py       sends alert email when track.yml fails repeatedly
lib/state_machine.py    pure transition logic (~20 unit tests)
lib/airports.py         nearest-airport-by-coord over ~48k OurAirports dataset
lib/email_html.py       multipart/alternative rendering with banner colours
lib/ntfy.py             push notifications to ntfy.sh
lib/timefmt.py          BST/UTC dual time formatting
config.json             watched aircraft + cadence/timezone settings
state.json              last-known state per aircraft (auto-committed)
flights/                event + flight history (auto-committed)
  events_YYYY-MM.jsonl  every emitted event
  flights_YYYY-MM.jsonl completed flight summaries with full position track
  backfill_*.jsonl      OpenSky historical data (no per-poll positions)
docs/                   GitHub Pages static dashboard
.github/workflows/      track, backfill, heartbeat, keepalive, test, failure-monitor
```

## Dashboard

The map shows two flight types:
- **Solid blue lines** = real position tracks captured by the tracker.
- **Dashed gray lines** = airport-to-airport straight lines from OpenSky backfill
  (no per-poll positions available historically).

## Reliability notes

- **GHA cron**: documented 5-min minimum; real-world ~10-15 min on free tier.
  Allow ~15-25 min from wheels-up to email.
- **Dual ADS-B source**: if adsb.lol returns nothing, the tracker falls back to
  airplanes.live before declaring the plane absent. Both being down at the same
  time is rare.
- **LADD/PIA**: adsb.lol, airplanes.live and OpenSky don't honor FAA privacy
  blocks because their data is community-contributed.
- **Commit churn**: state.json and flights/* are committed every run that
  produces a change. A typical 1-hour flight generates ~6 commits.
- **Schema-tolerant state machine**: an older state.json (missing recently-added
  fields) is backfilled with defaults on load, so upgrades don't crash a live run.

## License

No license declared — personal project. Fork freely.
