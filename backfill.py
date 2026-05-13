"""One-shot OpenSky 30-day flight history backfill.

Writes one JSONL line per flight into flights/backfill_<reg>.jsonl.
Reads tail/icao24 list from config.json.
Requires env vars: OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET.

OpenSky free-tier constraint: each /flights/aircraft query must span
no more than 2 UTC-day partitions. We loop one calendar day at a time.
"""

import datetime
import json
import os
import pathlib
import sys
import time

import requests

from lib import airports


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
FLIGHTS_DIR = ROOT / "flights"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_URL = "https://opensky-network.org/api/flights/aircraft"
DAY = 24 * 3600
DEFAULT_DAYS = 7
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5


def main():
    days = int(os.environ.get("BACKFILL_DAYS", DEFAULT_DAYS))
    client_id = os.environ["OPENSKY_CLIENT_ID"]
    client_secret = os.environ["OPENSKY_CLIENT_SECRET"]
    token = get_token(client_id, client_secret)

    config = json.loads(CONFIG_PATH.read_text())
    FLIGHTS_DIR.mkdir(exist_ok=True)

    midnight_utc = int(datetime.datetime.combine(
        datetime.datetime.now(datetime.timezone.utc).date(),
        datetime.time(0, 0), tzinfo=datetime.timezone.utc,
    ).timestamp())

    for ac in config["aircraft"]:
        reg = ac["registration"]
        icao24 = ac["icao24"].lower()
        out_path = FLIGHTS_DIR / f"backfill_{reg}.jsonl"
        print(f"\n=== Backfilling {reg} ({icao24}) for last {days} days ===")
        flights = pull_flights(token, icao24, midnight_utc, days)
        flights = dedupe_flights(flights)
        flights.sort(key=lambda f: f["firstSeen"])
        with out_path.open("w", encoding="utf-8") as f:
            for fl in flights:
                f.write(json.dumps(enrich(fl)) + "\n")
        print(f"wrote {len(flights)} flights to {out_path.relative_to(ROOT)}")
        for fl in flights:
            t = datetime.datetime.fromtimestamp(fl["firstSeen"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            dep = fl.get("estDepartureAirport") or "?"
            arr = fl.get("estArrivalAirport") or "?"
            dur = (fl["lastSeen"] - fl["firstSeen"]) // 60
            print(f"  {t}Z  {dep} -> {arr}  ({dur}min)")


def get_token(client_id: str, client_secret: str) -> str:
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = requests.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["access_token"]
        except requests.RequestException as e:
            last_exc = e
            print(f"  token attempt {attempt}/{RETRY_ATTEMPTS} failed: {e}", file=sys.stderr)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"OpenSky token fetch failed after {RETRY_ATTEMPTS} attempts: {last_exc}")


def pull_flights(token: str, icao24: str, midnight_utc: int, days: int) -> list[dict]:
    """Loop one UTC day at a time backward from today's midnight."""
    out = []
    for i in range(1, days + 1):
        end = midnight_utc - (i - 1) * DAY
        begin = end - DAY
        r = requests.get(
            API_URL,
            params={"icao24": icao24, "begin": begin, "end": end},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code == 404:
            continue  # no flights that day
        if r.status_code != 200:
            print(f"  {datetime.date.fromtimestamp(begin)}: HTTP {r.status_code} {r.text[:80]}", file=sys.stderr)
            continue
        try:
            chunk = r.json()
        except Exception:
            continue
        if chunk:
            out.extend(chunk)
        # Light politeness pause; OpenSky tolerates fast queries but no need to hammer.
        time.sleep(0.1)
    return out


def dedupe_flights(flights: list[dict], tolerance_seconds: int = 300) -> list[dict]:
    """OpenSky sometimes returns the same flight twice with slightly different
    finalization timestamps (off by seconds). Bucket flights whose firstSeen
    fall within `tolerance_seconds` of each other and keep the most complete copy.
    """
    flights = sorted(flights, key=lambda f: f["firstSeen"])
    result: list[dict] = []
    for f in flights:
        if result and abs(f["firstSeen"] - result[-1]["firstSeen"]) <= tolerance_seconds:
            cur = result[-1]
            if _completeness_score(f) > _completeness_score(cur):
                result[-1] = f
        else:
            result.append(f)
    return result


def _completeness_score(f: dict) -> int:
    return bool(f.get("estDepartureAirport")) + bool(f.get("estArrivalAirport"))


def enrich(f: dict) -> dict:
    """Add human-readable airport names where we can resolve ICAO codes."""
    out = dict(f)
    for k_in, k_out in [("estDepartureAirport", "departure_airport"),
                         ("estArrivalAirport", "arrival_airport")]:
        icao = f.get(k_in)
        if icao:
            a = airports.by_icao(icao)
            if a:
                out[k_out] = {"icao": icao, "name": a["name"], "city": a.get("city"),
                              "iso_country": a.get("iso_country")}
    out["duration_minutes"] = (f["lastSeen"] - f["firstSeen"]) // 60
    return out


if __name__ == "__main__":
    main()
