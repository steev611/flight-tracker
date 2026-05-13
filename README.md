# flight-tracker

Polls **adsb.lol** for one or more aircraft and emails you on takeoff / landing /
signal loss. Runs as a GitHub Actions cron, free, no servers. Backfills 30 days
of history from **OpenSky Network**.

Works for ADS-B Exchange–visible aircraft that mainstream trackers hide
(e.g. LADD/PIA-blocked tails) because adsb.lol and OpenSky publish unfiltered data.

## What it does

- Every ~10 min: hits `https://api.adsb.lol/v2/reg/<TAIL>` for each tracked aircraft.
- Classifies state: `airborne` / `ground` / `absent` (not heard).
- Emits events on transition: **takeoff**, **landing**, **signal_lost**.
- Sends an email per event via SMTP (Gmail-compatible).
- Logs every event to `flights/events_YYYY-MM.jsonl` and every completed flight to `flights/flights_YYYY-MM.jsonl` — committed straight to the repo.

## Setup

### 1. Add tails to `config.json`

```json
{
  "aircraft": [
    { "registration": "N83TY", "icao24": "ab57c1",
      "type": "Falcon 50EX", "owner": "Zoe Air Delaware Inc" }
  ]
}
```

Look up `icao24` (Mode-S hex) at `https://api.adsbdb.com/v0/aircraft/<TAIL>`.

### 2. Add repo secrets

`Settings → Secrets and variables → Actions → New repository secret`:

| Name | Example | Notes |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | |
| `SMTP_PORT` | `587` | STARTTLS |
| `SMTP_USER` | `you@gmail.com` | The Gmail address the email is sent from |
| `SMTP_PASS` | 16-char app password | Generate at https://myaccount.google.com/apppasswords (2FA required) |
| `NOTIFY_TO` | `you@gmail.com` | Where alerts go |
| `NOTIFY_FROM` | `you@gmail.com` | (Optional) defaults to `SMTP_USER` |
| `OPENSKY_CLIENT_ID` | `you@gmail.com-api-client` | Get at https://opensky-network.org/my-opensky/account |
| `OPENSKY_CLIENT_SECRET` | 32-char string | Same place — generate API client credentials |

### 3. Backfill history (optional, one-time)

`Actions → backfill → Run workflow`.  Pulls 30 days from OpenSky and commits
`flights/backfill_<TAIL>.jsonl`.

### 4. Tracker runs automatically

`track.yml` runs every 10 min via `schedule`. You can also `Run workflow` manually.
`keepalive.yml` pings the repo weekly so GitHub doesn't auto-pause the schedule.

## Local development

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                    # 24 tests
python tracker.py --dry-run                   # poll once, print, don't write/email
OPENSKY_CLIENT_ID=… OPENSKY_CLIENT_SECRET=… python backfill.py
```

## Files

```
tracker.py              main polling script
backfill.py             one-shot OpenSky history pull
lib/state_machine.py    pure transition logic (unit-tested)
lib/airports.py         nearest-airport-by-coord helper
lib/airports_data.json  ~48k airport dataset (OurAirports, large/medium/small)
config.json             watched aircraft list
state.json              last-known state per aircraft (auto-committed)
flights/                event + flight history (auto-committed)
.github/workflows/      track, backfill, keepalive
```

## Notes

- **GHA cron latency**: documented minimum is 5 min, real-world is closer to 10-15 min on free tier. Allow ~15-25 min from wheels-up to email.
- **Commit churn**: `state.json` and `flights/*.jsonl` are committed every run that produces a change. A typical 1-hour flight generates ~6 commits.
- **LADD/PIA**: adsb.lol and OpenSky don't honor FAA privacy-block requests because their data is community-contributed.
- **Coverage gaps**: low-altitude flights or remote regions may not be heard by any feeder; `signal_lost` events handle that case after 3 consecutive absent polls.
