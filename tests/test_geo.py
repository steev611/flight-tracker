import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.geo import haversine_nm, track_distance_nm, peak_altitude_ft, fmt_fl


def test_haversine_jfk_to_lhr_is_about_3000nm():
    # KJFK 40.6413, -73.7781 → EGLL 51.4700, -0.4543
    d = haversine_nm(40.6413, -73.7781, 51.4700, -0.4543)
    assert 2960 < d < 3020   # ~2998 nm

def test_track_distance_sums_segments():
    # Straight north from 40N → 41N → 42N at constant longitude
    track = [[40, -73, 30000, 0], [41, -73, 35000, 1], [42, -73, 38000, 2]]
    d = track_distance_nm(track)
    # 1 degree of latitude = 60 nm, so 2 segments * 60 = ~120nm
    assert 119 < d < 121

def test_peak_altitude():
    track = [[40, -73, 5000, 0], [41, -73, 41000, 1], [42, -73, 38000, 2]]
    assert peak_altitude_ft(track) == 41000

def test_peak_altitude_ignores_ground():
    track = [[40, -73, "ground", 0], [41, -73, 30000, 1]]
    assert peak_altitude_ft(track) == 30000

def test_fmt_fl_high():
    assert fmt_fl(41000) == "FL410"
    assert fmt_fl(35500) == "FL355"

def test_fmt_fl_low():
    assert fmt_fl(5000) == "5000 ft"

def test_fmt_fl_none():
    assert fmt_fl(None) == "—"

def test_track_distance_handles_dict_form():
    track = [{"lat": 40, "lon": -73, "alt": 30000, "ts": 0},
             {"lat": 41, "lon": -73, "alt": 35000, "ts": 1}]
    assert 59 < track_distance_nm(track) < 61
