"""Tests for the course-faithful confluence detector + retracement entry."""
from msf_trader.backtest.engine import run_backtest
from util import build, make_spec

# A clean down-move into a support pivot near 100, then a doji at the level.
# (idx1 prints the swing low @100 -> support; idx7 is the doji trigger.)
LONG_SETUP = [
    (110, 111, 108, 109),
    (109, 110, 100, 101),    # swing low @1 -> support 100
    (101, 109, 101, 108),
    (108, 112, 107, 111),    # close 111 (lookback anchor)
    (111, 112, 109, 110),
    (110, 111, 107, 108),
    (108, 109, 104, 105),
    (101, 101.4, 100, 101),  # idx7 doji at support, down-move -> LONG candidate
]


def _conf_spec(**over):
    base = dict(
        strategy_mode="confluence",
        ma_periods=(3,),
        sr_pivot_strength=1,
        sr_lookback_bars=50,
        move_lookback_bars=4,
        min_move_points=3.0,
        sr_proximity_points=2.0,
        zone_width_points=2.0,
        # isolate the pivot source so confluence is deterministic
        use_prior_day_levels=False,
        use_gap_levels=False,
        use_ma_levels=False,
        use_round_numbers=False,
        use_volume_confluence=False,
        min_confluence_factors=2,   # pivot(1) + trigger(1) = 2
        consolidation_min_bars=3,
        approach_lookback=6,
        entry_mode="retracement",
        retracement_fraction=0.5,
        entry_valid_bars=3,
        contracts=2,
        first_target_points=3.0,
        slippage_ticks=0,
        commission_per_contract=0.0,
        stop_buffer_ticks=0,
        daily_max_loss_dollars=1e9,
        point_value=50.0,
    )
    base.update(over)
    return make_spec(**base)


def test_retracement_entry_fills_then_targets():
    spec = _conf_spec()
    # signal candle idx7 range [100,101.4] -> 50% retrace limit = 100.7
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),   # idx8: low 100.5 <= 100.7 -> LIMIT fill @100.7
        (101, 104, 100.8, 103.8),   # idx9: high 104 >= 103.7 -> target1, breakeven
        (103.8, 104, 102, 103),     # idx10: last RTH bar -> EOD flat the runner
    ]
    res = run_backtest(build(spec, rows), spec)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.entry_price == 100.7                      # filled at the retracement, not next open
    assert [e.reason for e in t.exits] == ["target1", "eod"]
    # leg1 = (103.7-100.7)*50 = 150 ; runner = (103-100.7)*50 = 115
    assert round(t.pnl_dollars, 2) == 265.0


def test_confluence_gate_blocks_single_reason():
    # Require 3 reasons; a lone pivot zone + trigger only musters 2 -> no trade.
    spec = _conf_spec(min_confluence_factors=3)
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),
        (101, 104, 100.8, 103.8),
        (103.8, 104, 102, 103),
    ]
    res = run_backtest(build(spec, rows), spec)
    assert res.trades == []


def test_no_fill_when_price_never_retraces():
    spec = _conf_spec()
    # price runs away from the 100.7 limit and never comes back within the window
    rows = LONG_SETUP + [
        (102, 104, 101.0, 103.5),
        (103.5, 105, 102.5, 104.5),
        (104.5, 106, 103.5, 105.5),
    ]
    res = run_backtest(build(spec, rows), spec)
    assert res.trades == []
