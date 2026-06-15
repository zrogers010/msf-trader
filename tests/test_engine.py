from msf_trader.backtest.engine import _is_doji, run_backtest
from util import build, make_bar, make_spec
from datetime import datetime
from util import ET


def _doji_bar(o, h, l, c):
    spec = make_spec()
    return make_bar(spec, datetime(2026, 6, 1, 10, 0, tzinfo=ET), o, h, l, c)


def test_is_doji():
    assert _is_doji(_doji_bar(101, 101.5, 100, 101), 0.10)        # tiny body
    assert not _is_doji(_doji_bar(101, 109, 101, 108), 0.10)      # big body
    assert not _is_doji(_doji_bar(100, 100, 100, 100), 0.10)      # zero range


# Shared building blocks ------------------------------------------------------
LONG_SETUP = [
    (110, 111, 108, 109),
    (109, 110, 100, 101),    # swing low @1 -> support 100
    (101, 109, 101, 108),
    (108, 112, 107, 111),    # close 111 (lookback anchor)
    (111, 112, 109, 110),
    (110, 111, 107, 108),
    (108, 109, 104, 105),
    (101, 101.4, 100, 101),  # idx7 doji at support, down-move -> LONG signal
]

SHORT_SETUP = [
    (110, 112, 109, 111),
    (111, 120, 110, 112),        # swing high @1 -> resistance 120
    (112, 113, 111, 112),
    (112, 114, 111, 113),        # close 113 anchor
    (113, 116, 112, 115),
    (115, 118, 114, 117),
    (117, 121, 116, 120),
    (119.8, 120, 119.5, 119.8),  # idx7 doji at resistance, up-move -> SHORT signal
]


def test_long_scale_out_then_runner_trail():
    spec = make_spec()
    rows = LONG_SETUP + [
        (101, 104.5, 101.5, 103),   # idx8 entry@101; target +3 (=104) hit -> leg1; breakeven
        (103, 104, 102.5, 103.5),   # trail ratchets up
        (103.5, 106, 103, 105.5),
        (105.5, 106, 101, 101.5),   # idx11 close below trail stop -> runner exits
    ]
    bars = build(spec, rows)
    res = run_backtest(bars, spec)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.entry_price == 101.0
    assert [e.reason for e in t.exits] == ["target1", "trail_stop"]
    assert t.open_qty == 0
    # leg1: (104-101)*50*1 = 150 ; runner: (101.5-101)*50*1 = 25
    assert round(t.pnl_dollars, 2) == 175.0


def test_short_stops_out_before_target():
    spec = make_spec()
    rows = SHORT_SETUP + [
        (119.5, 121, 119.3, 120.5),  # idx8 entry@119.5; close 120.5 > signal high 120 -> stop
    ]
    bars = build(spec, rows)
    res = run_backtest(bars, spec)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "short"
    assert len(t.exits) == 1
    assert t.exits[0].reason == "stop"
    assert t.exits[0].qty == spec.contracts          # full size, no scale-out
    assert round(t.pnl_dollars, 2) == -100.0          # (119.5-120.5)*50*2


def test_no_shorts_midday():
    spec = make_spec()
    rows = SHORT_SETUP + [(119.5, 121, 119.3, 120.5)]
    # start at 11:10 ET -> entire sequence is midday (allow_short=False)
    bars = build(spec, rows, start="11:10")
    assert all(b.session == "midday" for b in bars)
    res = run_backtest(bars, spec)
    assert res.trades == []


def test_eod_flat_closes_open_position():
    spec = make_spec()
    rows = LONG_SETUP + [
        (101, 103, 100.5, 102),     # entry@101, no target (high<104), no stop
        (102, 103.5, 101, 102.5),
        (102.5, 103, 101.5, 102.8),  # last RTH bar -> EOD flat
    ]
    bars = build(spec, rows)
    res = run_backtest(bars, spec)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert len(t.exits) == 1
    assert t.exits[0].reason == "eod"
    assert t.exits[0].qty == spec.contracts
    assert round(t.pnl_dollars, 2) == 180.0           # (102.8-101)*50*2


def test_daily_max_loss_locks_out_further_entries():
    # Phase A: a short that loses ~$100. Phase B: a later valid long setup.
    rows = [
        (110, 112, 109, 111),
        (111, 120, 110, 112),        # resistance 120
        (112, 113, 111, 112),
        (112, 114, 111, 113),
        (113, 116, 112, 115),
        (115, 118, 114, 117),
        (117, 121, 116, 120),
        (119.8, 120, 119.5, 119.8),  # idx7 SHORT signal
        (119.5, 121, 119.3, 120.5),  # idx8 entry + stop (loss -100)
        (120, 121, 118, 119),
        (119, 120, 110, 111),        # swing low @10 -> support 110
        (111, 119, 111, 118),
        (118, 119, 117, 118),
        (118, 120, 117, 119),        # anchor close 119
        (119, 120, 116, 117),
        (117, 118, 113, 114),
        (111, 111.4, 110, 111),      # idx16 LONG signal at support
        (111, 112, 110.5, 111.5),    # idx17 entry
        (111.5, 112, 110, 111),      # idx18 last RTH -> EOD flat
    ]
    permissive = make_spec(daily_max_loss_dollars=1e9)
    locked = make_spec(daily_max_loss_dollars=50.0)

    res_permissive = run_backtest(build(permissive, rows), permissive)
    res_locked = run_backtest(build(locked, rows), locked)

    # With no cap: both the short and the later long fire.
    assert len(res_permissive.trades) == 2
    # With a $50 daily cap: the -$100 short trips the lockout, blocking the long.
    assert len(res_locked.trades) == 1
    assert res_locked.trades[0].direction == "short"
