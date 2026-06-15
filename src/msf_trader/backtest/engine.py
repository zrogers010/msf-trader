"""Bar-by-bar backtest engine for the reviewed /ES strategy.

Two detection modes (docs/STRATEGY_RULES_REVIEWED.md):
  - "confluence" (default, course-faithful):
        levels : multi-source S/R ZONES (levels.py)
        approach: straight-into = fade; consolidating-over = skip (§3)
        trigger : doji / tail / exhaustion at the zone (§4)
        gate    : require >= min_confluence_factors independent reasons (§5)
        entry   : LIMIT on a ~50% retracement into the signal candle (§6)
  - "legacy": the original single fractal-pivot doji + next-open fill (kept for
        comparison and for the mechanics unit tests).

Shared exit logic (both modes):
  stop   : a completed bar CLOSE beyond the far side of the signal candle
  target : first portion exits at +N points, then stop -> breakeven
  runner : remaining contract(s) trail the previous bar's extreme, never negative
  guards : midday = no shorts, flat by end of RTH, daily max-loss lockout
  costs  : per-contract commission + slippage on market fills (limit entries fill
           at the limit price)

Causality: signals use only completed bars; entries fill on a LATER bar; pivots
are only usable after they confirm. No look-ahead. Simulation only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import Bar, prior_day_levels
from .indicators import active_sr_levels, find_pivots, moving_averages, near_level
from .levels import active_zones, build_day_open, nearest_zone, opposing_zone
from .patterns import (
    approach_type,
    avg_volume,
    has_bearish_trigger,
    has_bullish_trigger,
    is_doji,
    move_length,
    reversal_direction,
    volume_spike,
)
from .spec import StrategySpec


@dataclass
class Pending:
    """A queued entry. limit_price=None means a market fill on the next bar.

    stop_price / target_price are precomputed by the essence detector (structural
    stop and structural first target). When None the engine falls back to the
    signal-candle stop and the fixed +first_target_points target.
    """
    direction: str
    signal_index: int
    limit_price: float | None
    expire_index: int
    stop_price: float | None = None
    target_price: float | None = None


@dataclass
class ExitLeg:
    ts: object
    price: float
    qty: int
    reason: str
    pnl_points: float
    pnl_dollars: float


@dataclass
class Trade:
    direction: str            # "long" | "short"
    entry_ts: object
    entry_price: float
    contracts: int
    signal_index: int
    stop_price: float
    exits: list[ExitLeg] = field(default_factory=list)
    open_qty: int = 0
    reasons: tuple[str, ...] = ()
    target1_price: float | None = None    # structural first target (essence mode)

    @property
    def closed(self) -> bool:
        return self.open_qty == 0

    @property
    def pnl_dollars(self) -> float:
        return sum(e.pnl_dollars for e in self.exits)

    @property
    def exit_ts(self):
        return self.exits[-1].ts if self.exits else None


def _is_doji(bar: Bar, frac: float) -> bool:
    # kept for backwards-compatible imports / tests
    return is_doji(bar, frac)


class BacktestEngine:
    def __init__(self, spec: StrategySpec):
        self.spec = spec

    def run(self, bars: list[Bar]) -> "BacktestResult":
        spec = self.spec
        tick = spec.tick_size
        slip = spec.slippage_ticks * tick
        limit_slip = spec.limit_slippage_ticks * tick

        mas = moving_averages(bars, spec.ma_periods)
        pivots = find_pivots(bars, spec.sr_pivot_strength)
        pday = prior_day_levels(bars)
        day_open = build_day_open(bars)
        max_ma = max(spec.ma_periods)

        # last tradable (RTH) bar index per date -> EOD flat + no late entries
        last_rth_idx: dict = {}
        for i, b in enumerate(bars):
            if b.session is not None:
                last_rth_idx[b.date] = i

        trades: list[Trade] = []
        position: Trade | None = None
        pending: Pending | None = None
        daily_pnl: dict = {}
        equity = 0.0
        equity_curve: list[tuple[object, float]] = []

        def commission(qty: int) -> float:
            return qty * spec.commission_per_contract

        def open_position(bar: Bar, p: Pending, fill_price: float) -> Trade:
            nonlocal equity
            sig = bars[p.signal_index]
            if p.stop_price is not None:
                stop = p.stop_price
            elif p.direction == "long":
                stop = sig.low - spec.stop_buffer_ticks * tick
            else:
                stop = sig.high + spec.stop_buffer_ticks * tick
            t = Trade(
                direction=p.direction,
                entry_ts=bar.ts,
                entry_price=fill_price,
                contracts=spec.contracts,
                signal_index=p.signal_index,
                stop_price=stop,
                open_qty=spec.contracts,
                target1_price=p.target_price,
            )
            t._first_target_done = False  # type: ignore[attr-defined]
            equity -= commission(spec.contracts)
            daily_pnl[bar.date] = daily_pnl.get(bar.date, 0.0) - commission(spec.contracts)
            return t

        def record_exit(trade: Trade, ts, price: float, qty: int, reason: str, market: bool):
            nonlocal equity
            use_slip = slip if market else limit_slip
            if trade.direction == "long":
                fill = price - use_slip
            else:
                fill = price + use_slip
            if trade.direction == "long":
                pts = (fill - trade.entry_price)
            else:
                pts = (trade.entry_price - fill)
            gross = pts * spec.point_value * qty
            net = gross - commission(qty)
            leg = ExitLeg(ts=ts, price=fill, qty=qty, reason=reason, pnl_points=pts, pnl_dollars=net)
            trade.exits.append(leg)
            trade.open_qty -= qty
            equity += net
            daily_pnl[ts.date()] = daily_pnl.get(ts.date(), 0.0) + net

        for i, bar in enumerate(bars):
            date = bar.date

            # 1) try to fill a pending entry (on a bar AFTER the signal)
            if pending is not None and position is None and i > pending.signal_index:
                if pending.limit_price is None:
                    # market: fill at this (first) bar's open
                    entry = bar.open + slip if pending.direction == "long" else bar.open - slip
                    position = open_position(bar, pending, entry)
                    pending = None
                else:
                    lp = pending.limit_price
                    reached = (bar.low <= lp) if pending.direction == "long" else (bar.high >= lp)
                    if reached:
                        fill = lp + limit_slip if pending.direction == "long" else lp - limit_slip
                        position = open_position(bar, pending, fill)
                        pending = None
                    elif i >= pending.expire_index:
                        pending = None  # never retraced to our entry -> pass on the trade

            # 2) manage an open position
            if position is not None:
                d = position.direction

                # 2a) first-target (limit) using intrabar extreme. Structural
                #     target (essence) if present, else fixed +N points.
                if not getattr(position, "_first_target_done") and spec.contracts > 1:
                    qty_first = max(1, spec.contracts // 2)
                    if d == "long":
                        tgt = position.target1_price if position.target1_price is not None \
                            else position.entry_price + spec.first_target_points
                        if bar.high >= tgt:
                            record_exit(position, bar.ts, tgt, qty_first, "target1", market=False)
                            position._first_target_done = True  # type: ignore[attr-defined]
                            position.stop_price = position.entry_price  # breakeven
                    else:
                        tgt = position.target1_price if position.target1_price is not None \
                            else position.entry_price - spec.first_target_points
                        if bar.low <= tgt:
                            record_exit(position, bar.ts, tgt, qty_first, "target1", market=False)
                            position._first_target_done = True  # type: ignore[attr-defined]
                            position.stop_price = position.entry_price

                # 2b) stop: either a CLOSE beyond the level (course-literal) or a
                #     resting stop ORDER touched intrabar (caps realized loss).
                if position.open_qty > 0:
                    if spec.stop_exec == "touch":
                        if d == "long":
                            stopped = bar.low <= position.stop_price
                        else:
                            stopped = bar.high >= position.stop_price
                        exit_px = position.stop_price
                    else:
                        stopped = (
                            bar.close < position.stop_price if d == "long" else bar.close > position.stop_price
                        )
                        exit_px = bar.close
                    if stopped:
                        reason = "trail_stop" if getattr(position, "_first_target_done") else "stop"
                        record_exit(position, bar.ts, exit_px, position.open_qty, reason, market=True)

                # 2c) EOD flat at last RTH bar of the day
                if position.open_qty > 0 and last_rth_idx.get(date) == i:
                    record_exit(position, bar.ts, bar.close, position.open_qty, "eod", market=True)

                # 2d) trail the runner to the prior bar extreme (after first target)
                if position.open_qty > 0 and getattr(position, "_first_target_done") and i > 0:
                    if spec.runner_trail == "prev_bar_extreme":
                        lb = max(1, spec.runner_trail_lookback)
                        buf = spec.runner_trail_buffer_points
                        window = bars[max(0, i - lb):i]
                        if d == "long":
                            cand = min(b.low for b in window) - buf
                            if spec.runner_never_negative:
                                cand = max(cand, position.entry_price)
                            position.stop_price = max(position.stop_price, cand)
                        else:
                            cand = max(b.high for b in window) + buf
                            if spec.runner_never_negative:
                                cand = min(cand, position.entry_price)
                            position.stop_price = min(position.stop_price, cand)
                    elif spec.runner_trail == "fixed_points":
                        if d == "long":
                            position.stop_price = max(position.stop_price, bar.close - spec.runner_trail_points)
                        else:
                            position.stop_price = min(position.stop_price, bar.close + spec.runner_trail_points)

                if position.closed:
                    trades.append(position)
                    position = None

            equity_curve.append((bar.ts, equity))

            # 3) look for a new entry signal (only when flat and no pending)
            if position is None and pending is None and i >= max_ma:
                if last_rth_idx.get(date) == i:  # nowhere to manage a late entry
                    continue
                if daily_pnl.get(date, 0.0) <= -spec.daily_max_loss_dollars:
                    continue
                if bar.session is None:
                    continue
                if spec.strategy_mode == "legacy":
                    direction = self._detect_signal(bars, i, pivots)
                    if direction is not None:
                        pending = Pending(direction, i, None, i + 1)
                elif spec.strategy_mode == "essence":
                    pending = self._detect_essence(bars, i, pivots, pday, mas, day_open)
                else:
                    pending = self._detect_confluence(bars, i, pivots, pday, mas, day_open)

        return BacktestResult(spec=spec, bars=bars, trades=trades, equity_curve=equity_curve, mas=mas)

    # --- legacy detector (single fractal pivot + doji, market next-open) -----
    def _detect_signal(self, bars: list[Bar], i: int, pivots) -> str | None:
        spec = self.spec
        bar = bars[i]
        if not is_doji(bar, spec.doji_body_fraction):
            return None
        if i - spec.move_lookback_bars < 0:
            return None
        levels = active_sr_levels(pivots, i, spec.sr_lookback_bars)
        if not levels and spec.require_reversal_at_sr:
            return None

        net_move = bar.close - bars[i - spec.move_lookback_bars].close
        tick = spec.tick_size
        if bar.allow_short and net_move >= spec.min_move_points:
            if near_level(bar.high, levels, spec.sr_proximity_points, "high"):
                stop_dist = (bar.high + spec.stop_buffer_ticks * tick) - bar.close
                if not spec.max_stop_points or stop_dist <= spec.max_stop_points:
                    return "short"
        if bar.allow_long and -net_move >= spec.min_move_points:
            if near_level(bar.low, levels, spec.sr_proximity_points, "low"):
                stop_dist = bar.close - (bar.low - spec.stop_buffer_ticks * tick)
                if not spec.max_stop_points or stop_dist <= spec.max_stop_points:
                    return "long"
        return None

    # --- confluence detector (zones + approach + trigger + gate + retrace) ---
    def _detect_confluence(self, bars, i, pivots, pday, mas, day_open) -> Pending | None:
        spec = self.spec
        bar = bars[i]
        if i - spec.move_lookback_bars < 0:
            return None

        zones = active_zones(bars, i, spec, pivots, pday, mas, day_open)
        if not zones:
            return None

        vol_avg = avg_volume(bars, i, spec.volume_lookback)
        trig = reversal_direction(bars, i, spec, vol_avg)
        net = bar.close - bars[i - spec.move_lookback_bars].close

        cand = None  # (direction, zone)
        if bar.allow_long and -net >= spec.min_move_points and has_bullish_trigger(trig):
            z = nearest_zone(zones, bar.low, spec.sr_proximity_points, "support")
            if z is not None:
                cand = ("long", z)
        if cand is None and bar.allow_short and net >= spec.min_move_points and has_bearish_trigger(trig):
            z = nearest_zone(zones, bar.high, spec.sr_proximity_points, "resistance")
            if z is not None:
                cand = ("short", z)
        if cand is None:
            return None
        direction, zone = cand

        # approach: price consolidating over the zone -> building energy to break
        # through -> do NOT fade it.
        if approach_type(bars, i, zone, spec) == "consolidating":
            return None

        # confluence: distinct level sources + the trigger candle + (volume)
        factors = zone.n_sources + 1  # +1 for the reversal trigger
        if spec.use_volume_confluence and (
            trig["exhaustion"] is not None or volume_spike(bars, i, spec, vol_avg)
        ):
            factors += 1
        if factors < spec.min_confluence_factors:
            return None

        rng = bar.high - bar.low
        tick = spec.tick_size
        if spec.entry_mode == "next_open" or rng <= 0:
            limit = None
        elif direction == "long":
            limit = bar.low + spec.retracement_fraction * rng
        else:
            limit = bar.high - spec.retracement_fraction * rng

        # CONFIRMED rule: skip if the structural stop is too far (risk too large).
        if direction == "long":
            stop = bar.low - spec.stop_buffer_ticks * tick
            entry_ref = limit if limit is not None else bar.close
            stop_dist = entry_ref - stop
        else:
            stop = bar.high + spec.stop_buffer_ticks * tick
            entry_ref = limit if limit is not None else bar.close
            stop_dist = stop - entry_ref
        if spec.max_stop_points and stop_dist > spec.max_stop_points:
            return None

        return Pending(direction, i, limit, i + spec.entry_valid_bars)

    # --- essence detector (docs/TRADING_ESSENCE.md geometry) -----------------
    def _detect_essence(self, bars, i, pivots, pday, mas, day_open) -> Pending | None:
        """Course-faithful geometry: a mature move INTO a multi-source level, a
        trigger candle, confluence + volume, entered near the extreme so the
        structural stop (close beyond the signal candle) is TINY, targeting the
        NEXT opposing zone, and only taken if reward:risk clears the gate."""
        spec = self.spec
        bar = bars[i]
        tick = spec.tick_size
        if i - spec.move_lookback_bars < 0:
            return None

        zones = active_zones(bars, i, spec, pivots, pday, mas, day_open)
        if not zones:
            return None

        vol_avg = avg_volume(bars, i, spec.volume_lookback)
        trig = reversal_direction(bars, i, spec, vol_avg)

        # 1) direction: fade a MATURE move that arrived at a level
        cand = None  # (direction, zone)
        if bar.allow_long and has_bullish_trigger(trig):
            z = nearest_zone(zones, bar.low, spec.sr_proximity_points, "support")
            if z is not None and move_length(bars, i, "down", spec.move_lookback_bars) >= spec.min_move_candles:
                cand = ("long", z)
        if cand is None and bar.allow_short and has_bearish_trigger(trig):
            z = nearest_zone(zones, bar.high, spec.sr_proximity_points, "resistance")
            if z is not None and move_length(bars, i, "up", spec.move_lookback_bars) >= spec.min_move_candles:
                cand = ("short", z)
        if cand is None:
            return None
        direction, zone = cand

        # 2) don't fade a consolidation that's building energy THROUGH the level
        if approach_type(bars, i, zone, spec) == "consolidating":
            return None

        # 3) confluence: distinct level sources + trigger + (volume/exhaustion)
        factors = zone.n_sources + 1
        if spec.use_volume_confluence and (
            trig["exhaustion"] is not None or volume_spike(bars, i, spec, vol_avg)
        ):
            factors += 1
        if factors < spec.min_confluence_factors:
            return None

        # 4) entry near the extreme -> TINY stop (close beyond the signal candle)
        rng = bar.high - bar.low
        if rng <= 0:
            return None
        if direction == "long":
            limit = bar.low + spec.retracement_fraction * rng
            stop = bar.low - spec.stop_buffer_ticks * tick
            stop_dist = limit - stop
        else:
            limit = bar.high - spec.retracement_fraction * rng
            stop = bar.high + spec.stop_buffer_ticks * tick
            stop_dist = stop - limit
        if stop_dist <= 0:
            return None
        if spec.max_stop_points and stop_dist > spec.max_stop_points:
            return None

        # 5) structural first target = the NEXT opposing zone, with a reward:risk gate
        opp = opposing_zone(zones, limit, direction, spec.target_min_points, spec.target_max_points)
        if opp is None:
            return None  # course: the target IS a level; no level -> no trade
        target_price, _ = opp
        if abs(target_price - limit) < spec.min_reward_risk * stop_dist:
            return None

        return Pending(direction, i, limit, i + spec.entry_valid_bars,
                       stop_price=stop, target_price=target_price)


@dataclass
class BacktestResult:
    spec: StrategySpec
    bars: list[Bar]
    trades: list[Trade]
    equity_curve: list[tuple[object, float]]
    mas: dict

    def metrics(self) -> dict:
        closed = [t for t in self.trades if t.closed]
        pnls = [t.pnl_dollars for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = sum(losses)
        net = sum(pnls)

        peak = 0.0
        max_dd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            max_dd = min(max_dd, eq - peak)

        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(closed)) if closed else 0.0,
            "net_pnl": net,
            "gross_profit": gross_win,
            "gross_loss": gross_loss,
            "profit_factor": (gross_win / abs(gross_loss)) if gross_loss else float("inf"),
            "avg_win": (gross_win / len(wins)) if wins else 0.0,
            "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
            "max_drawdown": max_dd,
        }


def run_backtest(bars: list[Bar], spec: StrategySpec) -> BacktestResult:
    return BacktestEngine(spec).run(bars)
