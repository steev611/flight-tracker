"""Fetch and format current METAR weather for an ICAO airport.

Uses aviationweather.gov's free public API — no key, no auth.
"""

from typing import Optional

import requests


METAR_URL = "https://aviationweather.gov/api/data/metar"
TIMEOUT = 8


def fetch(icao: str) -> Optional[dict]:
    """Return a dict with parsed + raw METAR, or None on failure / no data."""
    if not icao or len(icao) != 4:
        return None
    try:
        r = requests.get(METAR_URL,
                         params={"ids": icao, "format": "json", "taf": "false"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        records = r.json()
    except Exception:
        return None
    if not records:
        return None
    m = records[0]
    return {
        "icao": m.get("icaoId") or icao,
        "raw": m.get("rawOb"),
        "summary": _summary(m),
        "flight_category": m.get("fltCat"),
    }


def _summary(m: dict) -> str:
    """One-line plain-language summary."""
    bits: list[str] = []

    # Wind. wdir can be a number or the string "VRB" (variable).
    wdir = m.get("wdir")
    wspd = m.get("wspd")
    wgst = m.get("wgst")
    if wdir is not None and wspd is not None:
        dir_str = f"{int(wdir):03d}" if isinstance(wdir, (int, float)) else str(wdir)
        wind = f"{dir_str}/{int(wspd):02d}kt"
        if wgst:
            wind += f" gust {int(wgst)}kt"
        bits.append(wind)
    elif wspd is not None:
        bits.append(f"VRB/{int(wspd):02d}kt")

    # Temperature / dewpoint
    if m.get("temp") is not None:
        if m.get("dewp") is not None:
            bits.append(f"{int(m['temp'])}°C / dp {int(m['dewp'])}°C")
        else:
            bits.append(f"{int(m['temp'])}°C")

    # Visibility
    vis = m.get("visib")
    if vis:
        bits.append(f"vis {vis}")

    # Cloud layer (lowest)
    clouds = m.get("clouds") or []
    if clouds:
        lowest = clouds[0]
        cover = lowest.get("cover")
        base = lowest.get("base")
        if cover and base:
            bits.append(f"{cover} {int(base)}ft")
        elif cover:
            bits.append(cover)
    elif m.get("cover"):
        bits.append(m["cover"])

    # Pressure
    if m.get("altim"):
        bits.append(f"QNH {int(m['altim'])}")

    # Flight category if reported
    cat = m.get("fltCat")
    if cat:
        bits.append(cat)

    return " · ".join(bits) if bits else "weather data unavailable"
