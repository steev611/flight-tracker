"""One-shot: backfill real position tracks for the flights in flights/backfill_*.jsonl
using OpenSky's /tracks/all endpoint.

Converts OpenSky path entries [time, lat, lon, baro_alt_m, true_track, on_ground]
into the tracker's compact track format [lat, lon, alt_ft, ts, gs] and writes
synthetic 'flights_YYYY-MM.jsonl' records so the dashboard treats them the
same as live-tracker flights (with detail pages, real curved lines on the map,
etc).

Run locally — OpenSky's auth server blocks GHA runners. Idempotent: skips
flights already present in flights/flights_*.jsonl.
"""

import datetime
import json
import math
import os
import pathlib
import sys
import time

import requests

from lib import airports
from lib.geo import haversine_nm


ROOT = pathlib.Path(__file__).resolve().parent
FLIGHTS_DIR = ROOT / "flights"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
TRACKS_URL = "https://opensky-network.org/api/tracks/all"
METERS_TO_FEET = 3.28084


def main():
    cid = os.environ["OPENSKY_CLIENT_ID"]
    cs = os.environ["OPENSKY_CLIENT_SECRET"]
    token = _get_token(cid, cs)

    existing_flight_ids = _existing_flight_ids()
    written = 0
    skipped = 0

    for path in sorted(FLIGHTS_DIR.glob("backfill_*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                fid = _flight_id_for(rec)
                if fid in existing_flight_ids:
                    print(f"  skip {fid}: already have a tracker flight record")
                    skipped += 1
                    continue
                mid_ts = (rec["firstSeen"] + rec["lastSeen"]) // 2
                track = _fetch_track(token, rec["icao24"], mid_ts)
                if not track:
                    print(f"  skip {fid}: OpenSky returned no track")
                    skipped += 1
                    continue
                summary = _build_summary(rec, track, fid)
                _append_summary(summary)
                print(f"  wrote {fid}: {len(track)} points")
                written += 1
                # Be polite to OpenSky.
                time.sleep(0.5)

    print(f"\nDone. wrote={written}  skipped={skipped}")


def _existing_flight_ids() -> set[str]:
    out = set()
    for path in FLIGHTS_DIR.glob("flights_*.jsonl"):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                fid = rec.get("flight_id")
                if fid:
                    out.add(fid)
    return out


def _flight_id_for(backfill_rec: dict) -> str:
    dt = datetime.datetime.fromtimestamp(backfill_rec["firstSeen"], datetime.timezone.utc)
    return dt.strftime("flt_%Y%m%dT%H%M%SZ")


def _get_token(client_id: str, client_secret: str) -> str:
    r = requests.post(TOKEN_URL,
                      data={"grant_type": "client_credentials",
                            "client_id": client_id, "client_secret": client_secret},
                      timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


def _fetch_track(token: str, icao24: str, mid_ts: int) -> list[list]:
    r = requests.get(TRACKS_URL,
                     params={"icao24": icao24.lower(), "time": mid_ts},
                     headers={"Authorization": f"Bearer {token}"},
                     timeout=30)
    if r.status_code != 200:
        return []
    data = r.json()
    raw_path = data.get("path") or []
    # OpenSky path entries: [time, lat, lon, baro_alt_m, true_track_deg, on_ground]
    # Convert to our format: [lat, lon, alt_ft, ts, gs_kts]
    out = []
    prev = None
    for p in raw_path:
        ts, lat, lon, alt_m, _track, _on_ground = p
        alt_ft = int(alt_m * METERS_TO_FEET) if isinstance(alt_m, (int, float)) else None
        gs = None
        if prev and ts > prev[3]:
            dt_sec = ts - prev[3]
            d_nm = haversine_nm(prev[0], prev[1], lat, lon)
            gs = round(d_nm / (dt_sec / 3600.0))  # knots
        out.append([lat, lon, alt_ft, ts, gs])
        prev = out[-1]
    return out


def _build_summary(rec: dict, track: list[list], fid: str) -> dict:
    first = track[0]
    last = track[-1]
    dep_icao = rec.get("estDepartureAirport")
    arr_icao = rec.get("estArrivalAirport")
    dep_a = airports.by_icao(dep_icao) if dep_icao else None
    arr_a = airports.by_icao(arr_icao) if arr_icao else None
    landed_ts = rec["lastSeen"]
    takeoff_ts = rec["firstSeen"]
    return {
        "flight_id": fid,
        "registration": (rec.get("callsign") or "").strip() or "N83TY",
        "icao24": rec["icao24"],
        "takeoff_ts": takeoff_ts,
        "landed_ts": landed_ts,
        "landed_iso_utc": datetime.datetime.fromtimestamp(landed_ts, datetime.timezone.utc).isoformat(),
        "elapsed_seconds": landed_ts - takeoff_ts,
        "origin": (dep_a or {}).get("name") or dep_icao or "—",
        "origin_lat": (dep_a or {}).get("lat") or first[0],
        "origin_lon": (dep_a or {}).get("lon") or first[1],
        "destination": (arr_a or {}).get("name") or arr_icao or "—",
        "destination_lat": (arr_a or {}).get("lat") or last[0],
        "destination_lon": (arr_a or {}).get("lon") or last[1],
        "track": track,
        "_source": "opensky_tracks_backfill",
    }


def _append_summary(summary: dict) -> None:
    landed_dt = datetime.datetime.fromtimestamp(summary["landed_ts"], datetime.timezone.utc)
    month_key = landed_dt.strftime("%Y-%m")
    out_path = FLIGHTS_DIR / f"flights_{month_key}.jsonl"
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
