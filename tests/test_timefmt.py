import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.timefmt import fmt_dual, fmt_dual_from_ts


def test_fmt_dual_in_bst_summer():
    # 2026-07-01 12:00 UTC = 13:00 BST
    dt = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=datetime.timezone.utc)
    s = fmt_dual(dt, "Europe/London")
    assert "13:00 BST" in s
    assert "12:00 UTC" in s

def test_fmt_dual_in_gmt_winter():
    # 2026-01-01 12:00 UTC = 12:00 GMT
    dt = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    s = fmt_dual(dt, "Europe/London")
    assert "12:00 GMT" in s
    assert "12:00 UTC" in s

def test_fmt_dual_from_ts():
    s = fmt_dual_from_ts(1751371200, "Europe/London")  # 2025-07-01 12:00 UTC
    assert "BST" in s
    assert "UTC" in s
