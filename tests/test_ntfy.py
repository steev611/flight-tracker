import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.ntfy import _ascii_safe


def test_em_dash_folded_to_hyphen():
    assert _ascii_safe("Takeoff — KBGR") == "Takeoff - KBGR"

def test_right_arrow_folded():
    assert _ascii_safe("KBGR → EGAC") == "KBGR -> EGAC"

def test_smart_quotes_folded():
    assert _ascii_safe("“N83TY” ‘test’") == "\"N83TY\" 'test'"

def test_plain_ascii_unchanged():
    assert _ascii_safe("Takeoff from KJFK") == "Takeoff from KJFK"

def test_result_is_latin1_encodable():
    # The whole point: result must be safe to use in an HTTP header.
    out = _ascii_safe("—–‘’“”…→ ☃")  # last is snowman, unmapped
    out.encode("latin-1")  # would raise if not encodable
