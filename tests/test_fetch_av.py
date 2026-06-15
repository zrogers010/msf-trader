from datetime import date

from msf_trader.backtest.fetch_av import _recent_months


def test_recent_months_wraps_year_and_orders_ascending():
    got = _recent_months(4, today=date(2026, 2, 15))
    assert got == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_recent_months_single():
    assert _recent_months(1, today=date(2026, 6, 9)) == ["2026-06"]
