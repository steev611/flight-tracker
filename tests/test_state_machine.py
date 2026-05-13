import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.state_machine import (
    empty_state, classify_observation, step, Event,
)


# ---------- classify_observation ----------

def test_classify_airborne_with_altitude():
    obs = classify_observation({"alt_baro": 35000, "gs": 450, "lat": 40, "lon": -73}, ts=100)
    assert obs["kind"] == "airborne"

def test_classify_ground_literal():
    obs = classify_observation({"alt_baro": "ground", "gs": 0}, ts=100)
    assert obs["kind"] == "ground"

def test_classify_ground_low_speed_no_alt():
    obs = classify_observation({"gs": 3}, ts=100)
    assert obs["kind"] == "ground"

def test_classify_airborne_no_alt_but_fast():
    obs = classify_observation({"gs": 120}, ts=100)
    assert obs["kind"] == "airborne"

def test_classify_absent():
    obs = classify_observation(None, ts=100)
    assert obs["kind"] == "absent"


# ---------- step: transitions ----------

def test_unknown_to_airborne_emits_takeoff():
    s = empty_state()
    obs = classify_observation({"alt_baro": 20000, "gs": 300, "lat": 40, "lon": -73}, ts=1000)
    new, events = step(s, obs)
    assert new["status"] == "airborne"
    assert len(events) == 1
    assert events[0].type == "takeoff"
    assert events[0].details["prior_status"] == "unknown"
    assert new["current_flight_id"] is not None

def test_ground_to_airborne_emits_takeoff_with_origin():
    s = empty_state()
    obs_g = classify_observation({"alt_baro": "ground", "lat": 40, "lon": -73}, ts=1000)
    s, _ = step(s, obs_g)
    assert s["status"] == "ground"

    obs_a = classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40.1, "lon": -73.1}, ts=2000)
    s, events = step(s, obs_a)
    assert s["status"] == "airborne"
    assert events[0].type == "takeoff"
    assert events[0].details["departed_from"]["lat"] == 40

def test_airborne_to_ground_emits_landing():
    s = empty_state()
    obs_a = classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40, "lon": -73}, ts=1000)
    s, _ = step(s, obs_a)
    obs_g = classify_observation({"alt_baro": "ground", "lat": 41, "lon": -74}, ts=2000)
    s, events = step(s, obs_g)
    assert s["status"] == "ground"
    assert events[0].type == "landing"
    assert events[0].details["arrived_at"]["lat"] == 41

def test_airborne_stays_airborne_no_event():
    s = empty_state()
    obs1 = classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000)
    s, _ = step(s, obs1)
    obs2 = classify_observation({"alt_baro": 35000, "gs": 450, "lat": 41, "lon": -74}, ts=2000)
    s, events = step(s, obs2)
    assert s["status"] == "airborne"
    assert events == []

def test_absent_below_threshold_no_event():
    s = empty_state()
    obs_a = classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000)
    s, _ = step(s, obs_a)
    s, events = step(s, classify_observation(None, ts=2000))
    assert s["status"] == "absent"
    assert s["absent_count"] == 1
    assert events == []

def test_absent_threshold_while_airborne_emits_signal_lost():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000))
    s, _ = step(s, classify_observation(None, ts=2000))
    s, _ = step(s, classify_observation(None, ts=3000))
    s, events = step(s, classify_observation(None, ts=4000))
    assert s["absent_count"] == 3
    assert events[0].type == "signal_lost"
    assert events[0].details["last_known_position"]["lat"] == 40

def test_signal_lost_emitted_only_once():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000))
    s, _ = step(s, classify_observation(None, ts=2000))
    s, _ = step(s, classify_observation(None, ts=3000))
    s, _ = step(s, classify_observation(None, ts=4000))   # first emission
    s, events = step(s, classify_observation(None, ts=5000))
    assert events == []
    assert s["signal_lost_emitted"] is True

def test_absent_then_airborne_emits_takeoff_again():
    """A plane that lost signal in flight and then reappears airborne registers as a new takeoff cycle."""
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000))
    s, _ = step(s, classify_observation(None, ts=2000))
    s, _ = step(s, classify_observation(None, ts=3000))
    s, _ = step(s, classify_observation(None, ts=4000))  # signal_lost emitted
    s, events = step(s, classify_observation({"alt_baro": 28000, "gs": 380, "lat": 42, "lon": -75}, ts=5000))
    assert s["status"] == "airborne"
    assert any(e.type == "takeoff" for e in events)

def test_absent_then_ground_emits_landing():
    """Lost signal while airborne, then plane shows up on the ground (e.g., on-airport reacquisition)."""
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000))
    s, _ = step(s, classify_observation(None, ts=2000))
    s, events = step(s, classify_observation({"alt_baro": "ground", "lat": 41, "lon": -74}, ts=3000))
    assert s["status"] == "ground"
    assert events[0].type == "landing"

def test_prior_state_not_mutated():
    s = empty_state()
    s_snap = dict(s)
    _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73}, ts=1000))
    assert s == s_snap
