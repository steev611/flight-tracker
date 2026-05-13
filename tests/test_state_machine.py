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


# ---------- step: in_flight_progress ----------

def test_no_progress_event_immediately_after_takeoff():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40, "lon": -73}, ts=1000))  # takeoff
    # 10 minutes later — too soon for a progress update at default 30 min interval
    s, events = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 41, "lon": -74}, ts=1600))
    assert events == []

def test_progress_event_after_interval():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40, "lon": -73}, ts=1000))
    # 31 minutes later
    s, events = step(s, classify_observation({"alt_baro": 35000, "gs": 450, "lat": 42, "lon": -75}, ts=1000 + 31*60))
    assert len(events) == 1
    assert events[0].type == "in_flight_progress"
    assert events[0].details["elapsed_seconds"] == 31*60

def test_repeated_progress_events_each_interval():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40, "lon": -73}, ts=0))
    s, e1 = step(s, classify_observation({"alt_baro": 35000, "gs": 450, "lat": 41, "lon": -74}, ts=1800))
    assert any(e.type == "in_flight_progress" for e in e1)
    # 15 min later — too soon for the next
    s, e2 = step(s, classify_observation({"alt_baro": 36000, "gs": 460, "lat": 42, "lon": -75}, ts=2700))
    assert e2 == []
    # 30 min after the prior progress — yes
    s, e3 = step(s, classify_observation({"alt_baro": 37000, "gs": 470, "lat": 43, "lon": -76}, ts=3600))
    assert any(e.type == "in_flight_progress" for e in e3)

def test_landing_clears_takeoff_ts():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 5000, "gs": 200, "lat": 40, "lon": -73}, ts=1000))
    assert s["takeoff_ts"] == 1000
    s, events = step(s, classify_observation({"alt_baro": "ground", "lat": 45, "lon": 8}, ts=5500))
    assert events[0].type == "landing"
    assert events[0].details["elapsed_seconds"] == 4500
    assert s["takeoff_ts"] is None

# ---------- step: emergency squawk ----------

def test_emergency_squawk_7700_fires():
    s = empty_state()
    obs = classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73,
                                 "squawk": "7700"}, ts=1000)
    s, events = step(s, obs)
    assert any(e.type == "emergency_squawk" for e in events)
    em = next(e for e in events if e.type == "emergency_squawk")
    assert em.details["squawk"] == "7700"
    assert "emergency" in em.details["meaning"].lower()

def test_emergency_squawk_not_refire_same_code():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73,
                                          "squawk": "7700"}, ts=1000))
    s, events = step(s, classify_observation({"alt_baro": 30500, "gs": 405, "lat": 41, "lon": -74,
                                                "squawk": "7700"}, ts=2000))
    assert not any(e.type == "emergency_squawk" for e in events)

def test_emergency_squawk_refires_on_new_code():
    s = empty_state()
    s, _ = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73,
                                          "squawk": "7600"}, ts=1000))
    s, events = step(s, classify_observation({"alt_baro": 30500, "gs": 405, "lat": 41, "lon": -74,
                                                "squawk": "7700"}, ts=2000))
    assert any(e.type == "emergency_squawk" and e.details["squawk"] == "7700"
               for e in events)

def test_normal_squawk_no_event():
    s = empty_state()
    s, events = step(s, classify_observation({"alt_baro": 30000, "gs": 400, "lat": 40, "lon": -73,
                                                "squawk": "2000"}, ts=1000))
    assert not any(e.type == "emergency_squawk" for e in events)


def test_step_tolerates_old_state_missing_new_fields():
    """A state file written before in_flight fields existed should still work."""
    old_state = {
        "status": "airborne",
        "absent_count": 0,
        "prior_status": None,
        "last_position": {"lat": 40, "lon": -73, "alt": 30000, "gs": 400, "ts": 0},
        "current_flight_id": "flt_old",
        "signal_lost_emitted": False,
        # missing: takeoff_ts, last_inflight_progress_ts
    }
    new_state, events = step(old_state, classify_observation(
        {"alt_baro": 35000, "gs": 450, "lat": 41, "lon": -74}, ts=10000))
    # Should not crash and should not fire spurious progress (since takeoff_ts is unknown,
    # we treat current ts as the baseline).
    assert new_state["status"] == "airborne"
    assert events == []
