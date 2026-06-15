"""Helpers for building deterministic bar sequences in tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from msf_trader.backtest.data import Bar, tag_session
from msf_trader.backtest.spec import StrategySpec

ET = ZoneInfo("America/New_York")


def make_spec(**overrides) -> StrategySpec:
    """A small, fast spec with clean costs for exact P&L assertions.

    Defaults to the legacy detector so the engine-mechanics tests (entry on next
    open, +3 scale, trail, stops, EOD, daily lockout) stay deterministic. Pass
    strategy_mode="confluence" to exercise the course-faithful path.
    """
    base = dict(
        strategy_mode="legacy",
        ma_periods=(3,),
        sr_pivot_strength=1,
        sr_lookback_bars=50,
        move_lookback_bars=4,
        min_move_points=3.0,
        doji_body_fraction=0.10,
        sr_proximity_points=2.0,
        contracts=2,
        first_target_points=3.0,
        slippage_ticks=0,
        commission_per_contract=0.0,
        stop_buffer_ticks=0,
        daily_max_loss_dollars=1e9,
        point_value=50.0,
    )
    base.update(overrides)
    return StrategySpec(**base)


def make_bar(spec: StrategySpec, ts: datetime, o, h, l, c, v=100.0) -> Bar:
    win = tag_session(ts, spec.sessions)
    return Bar(
        ts=ts,
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=v,
        session=win.name if win else None,
        allow_long=win.allow_long if win else False,
        allow_short=win.allow_short if win else False,
    )


def build(spec: StrategySpec, rows, start="09:30", date="2026-06-01") -> list[Bar]:
    """rows: iterable of (open, high, low, close). 10-min bars from `start` ET."""
    hh, mm = (int(x) for x in start.split(":"))
    t = datetime.fromisoformat(date).replace(hour=hh, minute=mm, tzinfo=ET)
    bars = []
    for (o, h, l, c) in rows:
        bars.append(make_bar(spec, t, o, h, l, c))
        t = t + timedelta(minutes=10)
    return bars
