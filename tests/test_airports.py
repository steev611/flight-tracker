import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import airports


def test_by_icao_kjfk():
    a = airports.by_icao("KJFK")
    assert a is not None
    assert "Kennedy" in a["name"]
    assert a["iata"] == "JFK"

def test_nearest_at_jfk():
    a = airports.nearest(40.6413, -73.7781)
    assert a is not None
    assert a["icao"] == "KJFK"

def test_nearest_returns_none_in_atlantic():
    a = airports.nearest(30.0, -40.0, max_nm=5)
    assert a is None

def test_describe_position_falls_back_to_coords():
    s = airports.describe_position(30.0, -40.0, max_nm=5)
    assert "30.00" in s and "-40.00" in s

def test_describe_position_at_lfmq():
    a = airports.by_icao("LFMQ")
    s = airports.describe_position(a["lat"], a["lon"])
    assert "LFMQ" in s
