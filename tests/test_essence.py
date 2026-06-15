"""Tests for the essence detector (docs/TRADING_ESSENCE.md geometry):
extreme entry + tiny structural stop + STRUCTURAL target (next opposing zone)
+ reward:risk gate + candle-count maturity."""
from msf_trader.backtest.engine import run_backtest
from util import build, make_spec

# Down-move into support@100 (pivot low @idx1), with a resistance pivot @113
# (@idx3) above to serve as the LONG structural target. idx7 = doji at support.
LONG_SETUP = [
    (110, 111, 108, 109),
    (109, 110, 100, 101),     # idx1: swing low @100 -> support
    (101, 109, 101, 108),
    (108, 113, 107, 111),     # idx3: swing high @113 -> resistance (target)
    (111, 112, 109, 110),
    (110, 111, 107, 108),
    (108, 109, 104, 105),
    (101, 101.4, 100, 101),   # idx7: doji at support, mature down-move -> LONG
]


def _ess_spec(**over):
    base = dict(
        strategy_mode="essence",
        ma_periods=(3,),
        sr_pivot_strength=1,
        sr_lookback_bars=50,
        move_lookback_bars=4,
        sr_proximity_points=2.0,
        zone_width_points=2.0,
        # isolate the pivot source so confluence/targets are deterministic
        use_prior_day_levels=False,
        use_gap_levels=False,
        use_ma_levels=False,
        use_round_numbers=False,
        use_volume_confluence=False,
        min_confluence_factors=2,    # pivot(1) + trigger(1)
        consolidation_min_bars=3,
        approach_lookback=6,
        retracement_fraction=0.5,
        entry_valid_bars=3,
        contracts=2,
        target_mode="structural",
        min_reward_risk=1.5,
        target_min_points=2.0,
        target_max_points=40.0,
        min_move_candles=4,
        slippage_ticks=0,
        commission_per_contract=0.0,
        stop_buffer_ticks=0,
        max_stop_points=8.0,
        daily_max_loss_dollars=1e9,
        point_value=50.0,
    )
    base.update(over)
    return make_spec(**base)


def test_structural_target_is_the_next_opposing_zone():
    spec = _ess_spec()
    # signal idx7 range [100,101.4]; 50% retrace limit = 100.7; stop = 100 (low).
    # structural target = resistance zone @113 (NOT a fixed +3).
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),     # idx8: low 100.5 <= 100.7 -> LIMIT fill @100.7
        (101, 113.5, 100.8, 113),     # idx9: high 113.5 >= 113 -> target1, breakeven
        (113, 114, 112, 113),         # idx10: last RTH bar -> EOD flat the runner
    ]
    res = run_backtest(build(spec, rows), spec)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.entry_price == 100.7
    assert t.target1_price == 113.0                 # structural, not entry+3
    assert [e.reason for e in t.exits] == ["target1", "eod"]
    # leg1 = (113-100.7)*50 = 615 ; runner EOD = (113-100.7)*50 = 615
    assert round(t.pnl_dollars, 2) == 1230.0


def test_reward_risk_gate_blocks_thin_setups():
    # Demand an impossibly high reward:risk -> the 12.3/0.7 setup is rejected.
    spec = _ess_spec(min_reward_risk=100.0)
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),
        (101, 113.5, 100.8, 113),
        (113, 114, 112, 113),
    ]
    assert run_backtest(build(spec, rows), spec).trades == []


def test_no_trade_without_a_structural_target_in_range():
    # The only resistance (@113) is ~12 pts away; shrink the search window so no
    # opposing zone qualifies -> the course rule "target IS a level" -> no trade.
    spec = _ess_spec(target_max_points=5.0)
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),
        (101, 113.5, 100.8, 113),
        (113, 114, 112, 113),
    ]
    assert run_backtest(build(spec, rows), spec).trades == []


def test_immature_move_is_skipped():
    # Require a 10-candle move; only ~4 are available -> candle-count gate blocks.
    spec = _ess_spec(min_move_candles=10)
    rows = LONG_SETUP + [
        (101, 101.2, 100.5, 101),
        (101, 113.5, 100.8, 113),
        (113, 114, 112, 113),
    ]
    assert run_backtest(build(spec, rows), spec).trades == []
