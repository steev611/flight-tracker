"""Geometry helpers for derived flight stats."""

import math
from typing import Sequence


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R_NM = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R_NM * math.asin(math.sqrt(a))


def track_distance_nm(track: Sequence[Sequence]) -> float:
    """Sum great-circle distance through consecutive valid points.
    `track` entries are [lat, lon, alt, ts] (or {'lat':, 'lon':, ...} dicts)."""
    pts = [_lat_lon(p) for p in track]
    pts = [p for p in pts if p is not None]
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += haversine_nm(a[0], a[1], b[0], b[1])
    return total


def peak_altitude_ft(track: Sequence[Sequence]) -> int | None:
    best = None
    for p in track:
        alt = _alt(p)
        if isinstance(alt, (int, float)) and (best is None or alt > best):
            best = int(alt)
    return best


def _lat_lon(p) -> tuple[float, float] | None:
    if isinstance(p, dict):
        lat, lon = p.get("lat"), p.get("lon")
    else:
        lat, lon = (p[0] if len(p) > 0 else None), (p[1] if len(p) > 1 else None)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _alt(p):
    if isinstance(p, dict):
        return p.get("alt")
    return p[2] if len(p) > 2 else None


def fmt_fl(alt_ft: int | None) -> str:
    """Format an altitude (feet) as flight level (FL370) when above 18000."""
    if alt_ft is None:
        return "—"
    if alt_ft >= 18000:
        return f"FL{int(alt_ft) // 100:03d}"
    return f"{int(alt_ft)} ft"
