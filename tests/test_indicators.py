from msf_trader.backtest.indicators import (
    active_sr_levels,
    find_pivots,
    near_level,
    sma,
)
from util import build, make_spec


def test_sma_trailing_and_warmup():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert sma([10], 3) == [None]
    assert sma([1, 2, 3], 0) == [None, None, None]


def test_find_pivots_identifies_swing_low_and_confirm_lag():
    spec = make_spec()
    bars = build(spec, [
        (110, 111, 108, 109),
        (109, 110, 100, 101),   # swing low @ idx1
        (101, 109, 101, 108),
    ])
    pivots = find_pivots(bars, strength=1)
    lows = [p for p in pivots if p.kind == "low"]
    assert any(p.index == 1 and p.price == 100 for p in lows)
    piv = next(p for p in lows if p.index == 1)
    # strength=1 -> confirmed one bar later (causal)
    assert piv.confirmed_at == 2


def test_active_sr_levels_respects_confirmation_causality():
    spec = make_spec()
    bars = build(spec, [
        (110, 111, 108, 109),
        (109, 110, 100, 101),   # swing low @1, confirmed @2
        (101, 109, 101, 108),
        (108, 109, 107, 108),
    ])
    pivots = find_pivots(bars, strength=1)
    # not yet confirmed at index 1
    assert active_sr_levels(pivots, current_index=1, lookback_bars=50) == []
    # confirmed by index 2
    levels = active_sr_levels(pivots, current_index=2, lookback_bars=50)
    assert (100.0, "low") in levels


def test_near_level():
    levels = [(100.0, "low"), (120.0, "high")]
    assert near_level(101.0, levels, proximity=2.0, kind="low")
    assert not near_level(105.0, levels, proximity=2.0, kind="low")
    assert near_level(119.5, levels, proximity=2.0, kind="high")
    # wrong kind filtered out
    assert not near_level(100.5, levels, proximity=2.0, kind="high")
