import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.metar import _summary


def test_summary_full():
    m = {
        "wdir": 270, "wspd": 12, "temp": 16, "dewp": 1,
        "visib": "6+", "clouds": [{"cover": "SCT", "base": 3500}],
        "altim": 1010, "fltCat": "VFR",
    }
    s = _summary(m)
    assert "270/12kt" in s
    assert "16°C" in s
    assert "vis 6+" in s
    assert "SCT 3500ft" in s
    assert "QNH 1010" in s
    assert "VFR" in s

def test_summary_with_gust():
    m = {"wdir": 230, "wspd": 18, "wgst": 28, "temp": 14, "altim": 1015}
    s = _summary(m)
    assert "230/18kt" in s
    assert "gust 28kt" in s

def test_summary_clear_sky_uses_cover_field():
    m = {"wdir": 270, "wspd": 12, "temp": 16, "clouds": [], "cover": "CLR", "altim": 1010}
    s = _summary(m)
    assert "CLR" in s

def test_summary_handles_missing_fields():
    s = _summary({})
    assert s == "weather data unavailable"

def test_summary_handles_variable_wind():
    m = {"wdir": "VRB", "wspd": 3, "temp": 16}
    s = _summary(m)
    assert "VRB/03kt" in s
