"""Nearest-airport lookup over a bundled OurAirports dataset."""

import json
import math
import pathlib
from functools import lru_cache
from typing import Optional


_DATA_PATH = pathlib.Path(__file__).parent / "airports_data.json"


@lru_cache(maxsize=1)
def _load() -> list[dict]:
    with _DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def by_icao(icao: str) -> Optional[dict]:
    icao = icao.upper()
    for a in _load():
        if a["icao"] == icao:
            return a
    return None


def nearest(lat: float, lon: float, max_nm: float = 5.0) -> Optional[dict]:
    """Return the closest airport to (lat, lon) within `max_nm` nautical miles, or None."""
    best = None
    best_dist = float("inf")
    for a in _load():
        d = _haversine_nm(lat, lon, a["lat"], a["lon"])
        if d < best_dist:
            best_dist = d
            best = a
    if best is None or best_dist > max_nm:
        return None
    return {**best, "distance_nm": round(best_dist, 2)}


def describe_position(lat: Optional[float], lon: Optional[float], max_nm: float = 5.0) -> str:
    """Human-readable place name for a coordinate, falling back to lat/lon."""
    if lat is None or lon is None:
        return "unknown location"
    a = nearest(lat, lon, max_nm=max_nm)
    if a:
        bits = [a["icao"]]
        if a.get("name"):
            bits.append(a["name"])
        elif a.get("city"):
            bits.append(a["city"])
        return " ".join(bits)
    return f"{lat:.4f}, {lon:.4f}"


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R_NM = 3440.065  # Earth radius in nautical miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R_NM * math.asin(math.sqrt(a))
