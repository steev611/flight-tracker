"""Smoke tests for tracker.render_email — no network, no SMTP."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.state_machine import Event
from tracker import render_email


AC = {"registration": "N83TY", "icao24": "ab57c1",
      "type": "Falcon 50EX", "owner": "Zoe Air Delaware Inc"}


def test_email_takeoff_from_known_origin():
    ev = Event("takeoff", {
        "departed_from": {"lat": 40.6413, "lon": -73.7781},   # JFK
        "position": {"lat": 40.7, "lon": -73.9, "alt": 5000, "gs": 220, "ts": 1},
        "prior_status": "ground",
    })
    subj, body, html_body = render_email(AC, ev)
    assert "Takeoff" in subj
    assert "KJFK" in subj or "KJFK" in body
    assert "Falcon 50EX" in body
    assert "globe.adsb.lol" in body

def test_email_takeoff_from_unknown():
    ev = Event("takeoff", {
        "departed_from": None,
        "position": {"lat": 40.7, "lon": -73.9, "alt": 35000, "gs": 450, "ts": 1},
        "prior_status": "unknown",
    })
    subj, body, html_body = render_email(AC, ev)
    assert "Takeoff" in subj
    assert "unknown" in body.lower() or "acquired" in body.lower()

def test_email_landing():
    ev = Event("landing", {
        "arrived_at": {"lat": 45.6306, "lon": 8.7281, "alt": "ground", "gs": 0, "ts": 1},  # LIMC Malpensa
        "flight_id": "flt_X",
    })
    subj, body, html_body = render_email(AC, ev)
    assert "Landed" in subj
    assert "LIMC" in subj or "LIMC" in body

def test_email_signal_lost():
    ev = Event("signal_lost", {
        "last_known_position": {"lat": 45.6, "lon": 8.7, "alt": 12000, "gs": 350, "ts": 1},
        "absent_polls": 3,
        "flight_id": "flt_X",
    })
    subj, body, html_body = render_email(AC, ev)
    assert "Signal lost" in subj
    assert "outside ADS-B coverage" in body

def test_html_body_returned_for_known_events():
    ev = Event("takeoff", {
        "departed_from": {"lat": 40.6413, "lon": -73.7781},
        "position": {"lat": 40.7, "lon": -73.9, "alt": 5000, "gs": 220, "ts": 1},
        "prior_status": "ground",
    })
    subj, body, html_body = render_email(AC, ev)
    assert html_body is not None
    assert "<!DOCTYPE html>" in html_body
    assert "TAKEOFF" in html_body
    assert AC["registration"] in html_body
