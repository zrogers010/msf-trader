"""Deterministic daily order plan for the RSI(2) swing strategy.

This is the *brain*: given fresh daily bars + current positions, it returns the
exact list of orders to place TODAY (entries, exits, sizes). It is pure and
side-effect-free so it can be unit-tested and dry-run before any real money or
broker connection is involved. Execution (paper or Robinhood MCP) is handled
separately in `broker.py` / `runner.py`.

Intended cadence: run once per day ~15 minutes before the close.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .rsi2 import Rsi2Params, wilder_rsi


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_date: str            # ISO date
    bars_held: int = 0         # trading days held so far


@dataclass
class PlannedOrder:
    symbol: str
    action: str                # "BUY" | "SELL"
    reason: str
    price: float               # latest/ref price used for sizing
    notional: float | None = None   # for BUYs (fractional dollar order)
    quantity: float | None = None   # for SELLs (close full position)

    def describe(self) -> str:
        if self.action == "BUY":
            return f"BUY  ${self.notional:,.0f} {self.symbol} @~{self.price:.2f}  [{self.reason}]"
        return f"SELL {self.quantity:g} {self.symbol} @~{self.price:.2f}  [{self.reason}]"


def _indicators(df: pd.DataFrame, p: Rsi2Params):
    c = df["close"].reset_index(drop=True)
    return {
        "close": float(c.iloc[-1]),
        "rsi2": float(wilder_rsi(c, p.rsi_period).iloc[-1]),
        "sma_regime": float(c.rolling(p.regime_sma).mean().iloc[-1]),
        "sma_exit": float(c.rolling(p.exit_sma).mean().iloc[-1]),
        "n": len(c),
    }


def compute_daily_plan(
    asof: date,
    bars: dict[str, pd.DataFrame],
    positions: list[Position],
    equity: float,
    cash: float,
    params: Rsi2Params = Rsi2Params(),
    target_positions: int = 5,
    min_notional: float = 50.0,
    fixed_notional: float | None = None,
) -> list[PlannedOrder]:
    """Return today's orders. Exits are evaluated first (freeing slots + cash),
    then the most-oversold qualifying names fill the open slots, each sized either
    to a `fixed_notional` dollar amount (if given) or to `max_weight` of equity,
    capped by available cash.

    `target_positions` is the number of concurrent slots. `fixed_notional` (e.g.
    $1000/trade) does not compound with equity; leave it None for %-of-equity
    sizing. `bars[symbol]` must be daily OHLCV through `asof`."""
    orders: list[PlannedOrder] = []
    held = {pos.symbol: pos for pos in positions}

    # 1) EXITS — close on the bounce above the exit SMA, or at the time stop.
    remaining_holds = 0
    cash_avail = cash
    for pos in positions:
        df = bars.get(pos.symbol)
        if df is None or len(df) < params.exit_sma:
            remaining_holds += 1
            continue
        ind = _indicators(df, params)
        hit_target = ind["close"] > ind["sma_exit"]
        hit_timestop = pos.bars_held >= params.max_hold
        if hit_target or hit_timestop:
            reason = "exit: close>SMA%d" % params.exit_sma if hit_target else "exit: %dd time stop" % params.max_hold
            orders.append(PlannedOrder(pos.symbol, "SELL", reason,
                                       price=ind["close"], quantity=pos.quantity))
            cash_avail += pos.quantity * ind["close"]
        else:
            remaining_holds += 1

    # 2) ENTRIES — oversold dip inside an uptrend, for names we don't already hold.
    candidates: list[tuple[float, str, float]] = []  # (rsi2, symbol, price)
    for sym, df in bars.items():
        if sym in held:
            continue
        if df is None or len(df) < params.regime_sma:
            continue
        ind = _indicators(df, params)
        if ind["close"] > ind["sma_regime"] and ind["rsi2"] < params.rsi_buy:
            candidates.append((ind["rsi2"], sym, ind["close"]))
    candidates.sort(key=lambda x: x[0])  # most oversold first

    slots = max(0, target_positions - remaining_holds)
    per_name = fixed_notional if fixed_notional else params.max_weight * equity
    for rsi2, sym, price in candidates[:slots]:
        notional = min(per_name, cash_avail)
        if notional < min_notional:
            break
        orders.append(PlannedOrder(sym, "BUY", f"entry: RSI2={rsi2:.0f}<{params.rsi_buy:g}, above SMA{params.regime_sma}",
                                   price=price, notional=round(notional, 2)))
        cash_avail -= notional

    return orders
