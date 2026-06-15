from datetime import datetime

from msf_trader.backtest.levels import Zone
from msf_trader.backtest.patterns import approach_type, is_doji, tail_signal
from util import ET, build, make_bar, make_spec


def _bar(o, h, l, c):
    spec = make_spec()
    return make_bar(spec, datetime(2026, 6, 1, 10, 0, tzinfo=ET), o, h, l, c)


def test_is_doji():
    assert is_doji(_bar(101, 101.5, 100, 101), 0.10)
    assert not is_doji(_bar(101, 109, 101, 108), 0.10)
    assert not is_doji(_bar(100, 100, 100, 100), 0.10)  # zero range


def test_tail_signal_bullish():
    # body in the UPPER third, long lower tail -> bullish rejection of lows
    assert tail_signal(_bar(109, 110, 100, 109.5), 1 / 3) == "bullish"


def test_tail_signal_bearish():
    # body in the LOWER third, long upper tail -> bearish rejection of highs
    assert tail_signal(_bar(101, 110, 100, 100.5), 1 / 3) == "bearish"


def test_tail_signal_none():
    # body spanning the middle is not a tail candle
    assert tail_signal(_bar(104, 110, 100, 106), 1 / 3) is None


def test_approach_consolidating_vs_straight_in():
    spec = make_spec(strategy_mode="confluence", approach_lookback=6,
                     consolidation_min_bars=3, sr_proximity_points=2.0)
    zone = Zone(100.0, 100.0, "support", ("pivot",))

    # consolidating: most prior closes sit on the zone -> building energy
    consolidating = build(spec, [
        (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
        (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
        (100, 101, 99, 100),  # i=6 signal bar
    ])
    assert approach_type(consolidating, 6, zone, spec) == "consolidating"

    # straight_in: price was elsewhere and only just arrived at the zone
    straight = build(spec, [
        (110, 111, 109, 110), (108, 109, 107, 108), (106, 107, 105, 106),
        (105, 106, 104, 105), (104, 105, 103, 104), (102, 103, 101, 102),
        (101, 101.4, 100, 101),  # i=6 signal bar at the zone
    ])
    assert approach_type(straight, 6, zone, spec) == "straight_in"
