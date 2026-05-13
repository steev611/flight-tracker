"""State transitions for tracked aircraft.

Pure functions only — no I/O. Easy to unit-test.

State object schema:
    {
        "status": "unknown" | "ground" | "airborne" | "absent",
        "absent_count": int,
        "prior_status": "ground" | "airborne" | None,   # status before going absent
        "last_position": {"lat": float, "lon": float, "alt": int|"ground",
                          "gs": float, "ts": int} | None,
        "current_flight_id": str | None,
        "signal_lost_emitted": bool,
    }

Observation object schema (built by tracker.py from adsb.lol payload):
    {
        "kind": "airborne" | "ground" | "absent",
        "lat": float | None,
        "lon": float | None,
        "alt": int | "ground" | None,
        "gs": float | None,
        "ts": int,
    }
"""

from dataclasses import dataclass, field
from typing import Optional


GROUND_SPEED_AIRBORNE_KTS_DEFAULT = 50
ABSENCE_THRESHOLD_POLLS_DEFAULT = 3
INFLIGHT_PROGRESS_INTERVAL_SECONDS_DEFAULT = 1800  # 30 min

# https://en.wikipedia.org/wiki/Transponder_codes#Emergency_codes
EMERGENCY_SQUAWKS = {
    "7500": "Hijacking / unlawful interference",
    "7600": "Lost radio communications",
    "7700": "General emergency",
}


@dataclass
class Event:
    type: str  # "takeoff" | "landing" | "signal_lost" | "signal_returned"
    details: dict = field(default_factory=dict)


def empty_state() -> dict:
    return {
        "status": "unknown",
        "absent_count": 0,
        "prior_status": None,
        "last_position": None,
        "current_flight_id": None,
        "signal_lost_emitted": False,
        "last_inflight_progress_ts": None,
        "takeoff_ts": None,
        "last_squawk": None,
    }


def classify_observation(ac_entry: Optional[dict], ts: int,
                         airborne_kts: int = GROUND_SPEED_AIRBORNE_KTS_DEFAULT) -> dict:
    """Convert a raw adsb.lol `ac` array entry (or None) into our observation.

    Robust to messy fields: alt_baro can be a number, the literal string
    "ground", or missing; gs may be missing.
    """
    if ac_entry is None:
        return {"kind": "absent", "lat": None, "lon": None, "alt": None,
                "gs": None, "ts": ts, "squawk": None}

    alt = ac_entry.get("alt_baro")
    gs = ac_entry.get("gs")
    lat = ac_entry.get("lat")
    lon = ac_entry.get("lon")
    squawk = ac_entry.get("squawk")

    if alt == "ground":
        kind = "ground"
    elif isinstance(alt, (int, float)) and alt > 0:
        kind = "airborne"
    elif isinstance(gs, (int, float)) and gs >= airborne_kts:
        kind = "airborne"
    else:
        # No clear airborne signal; treat as ground.
        kind = "ground"

    return {"kind": kind, "lat": lat, "lon": lon, "alt": alt, "gs": gs,
            "ts": ts, "squawk": squawk}


def step(prior: dict, obs: dict,
         absence_threshold: int = ABSENCE_THRESHOLD_POLLS_DEFAULT,
         inflight_interval_seconds: int = INFLIGHT_PROGRESS_INTERVAL_SECONDS_DEFAULT,
         ) -> tuple[dict, list[Event]]:
    """Pure transition: given prior state + new observation, return (new_state, events).

    `prior` is never mutated. Backfills missing keys for older state files so
    new fields (added later) don't crash on first run.
    """
    # Backfill any missing fields from older state-file schema versions.
    base = empty_state()
    base.update(prior)
    prior = base

    new = dict(prior)
    events: list[Event] = []
    status = prior["status"]
    obs_kind = obs["kind"]

    if obs_kind == "airborne":
        new["last_position"] = _position_from_obs(obs)
        new["absent_count"] = 0
        new["prior_status"] = None
        new["signal_lost_emitted"] = False
        if status != "airborne":
            # Anything → airborne: takeoff event.
            departed_from = _position_summary(prior.get("last_position")) \
                if status == "ground" else None
            events.append(Event("takeoff", {
                "departed_from": departed_from,
                "position": new["last_position"],
                "prior_status": status,
            }))
            new["current_flight_id"] = _new_flight_id(obs["ts"])
            new["takeoff_ts"] = obs["ts"]
            new["last_inflight_progress_ts"] = obs["ts"]  # reset clock so first progress is ~interval later
        else:
            # Airborne → airborne: maybe time for a progress update.
            last_progress = prior.get("last_inflight_progress_ts")
            if last_progress is None:
                last_progress = prior.get("takeoff_ts")
            if last_progress is None:
                last_progress = obs["ts"]
            if obs["ts"] - last_progress >= inflight_interval_seconds:
                events.append(Event("in_flight_progress", {
                    "position": new["last_position"],
                    "takeoff_ts": prior.get("takeoff_ts"),
                    "elapsed_seconds": obs["ts"] - (prior.get("takeoff_ts") or obs["ts"]),
                    "flight_id": prior.get("current_flight_id"),
                }))
                new["last_inflight_progress_ts"] = obs["ts"]
        new["status"] = "airborne"

    elif obs_kind == "ground":
        was_airborne = (status == "airborne"
                        or (status == "absent" and prior.get("prior_status") == "airborne"))
        new["last_position"] = _position_from_obs(obs)
        new["absent_count"] = 0
        new["prior_status"] = None
        if was_airborne:
            events.append(Event("landing", {
                "arrived_at": new["last_position"],
                "flight_id": prior.get("current_flight_id"),
                "takeoff_ts": prior.get("takeoff_ts"),
                "elapsed_seconds": (obs["ts"] - prior["takeoff_ts"]) if prior.get("takeoff_ts") else None,
            }))
            new["current_flight_id"] = None
            new["takeoff_ts"] = None
            new["last_inflight_progress_ts"] = None
        new["status"] = "ground"
        new["signal_lost_emitted"] = False

    elif obs_kind == "absent":
        # Lost the signal this poll.
        if status != "absent":
            new["prior_status"] = status
        new["absent_count"] = prior["absent_count"] + 1
        new["status"] = "absent"
        if (new["prior_status"] == "airborne"
                and new["absent_count"] >= absence_threshold
                and not prior.get("signal_lost_emitted")):
            events.append(Event("signal_lost", {
                "last_known_position": prior.get("last_position"),
                "absent_polls": new["absent_count"],
                "flight_id": prior.get("current_flight_id"),
            }))
            new["signal_lost_emitted"] = True

    # Emergency squawk: fire when the squawk transitions INTO an emergency code.
    # We watch for changes so we don't spam alerts on every poll during the event.
    new_squawk = obs.get("squawk")
    new["last_squawk"] = new_squawk
    if (new_squawk in EMERGENCY_SQUAWKS
            and new_squawk != prior.get("last_squawk")):
        events.append(Event("emergency_squawk", {
            "squawk": new_squawk,
            "meaning": EMERGENCY_SQUAWKS[new_squawk],
            "position": _position_from_obs(obs) if obs_kind != "absent" else None,
            "flight_id": prior.get("current_flight_id"),
        }))

    return new, events


def _position_from_obs(obs: dict) -> dict:
    return {
        "lat": obs.get("lat"),
        "lon": obs.get("lon"),
        "alt": obs.get("alt"),
        "gs": obs.get("gs"),
        "ts": obs["ts"],
    }


def _position_summary(pos: Optional[dict]) -> Optional[dict]:
    if pos is None:
        return None
    return {"lat": pos.get("lat"), "lon": pos.get("lon"), "ts": pos.get("ts")}


def _new_flight_id(ts: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("flt_%Y%m%dT%H%M%SZ")
