"""Indicators and structure detection for the backtester.

- Simple moving averages (CONFIRMED: 20/50/200 on the 10-min chart).
- Swing-pivot support/resistance (ASSUMPTION: the course gives no algorithmic
  S/R definition, so we use a standard fractal-pivot method, tunable via spec).

All functions are causal: index i only uses information available up to i (or,
for confirmed pivots, a pivot is only *known* `strength` bars after it forms).
"""
from __future__ import annotations

from dataclasses import dataclass

from .data import Bar


def sma(values: list[float], period: int) -> list[float | None]:
    """Trailing SMA; None until enough data. Causal."""
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def moving_averages(bars: list[Bar], periods: tuple[int, ...]) -> dict[int, list[float | None]]:
    closes = [b.close for b in bars]
    return {p: sma(closes, p) for p in periods}


@dataclass
class Pivot:
    index: int
    price: float
    kind: str          # "high" | "low"
    confirmed_at: int  # bar index at which this pivot becomes known (index + strength)


def find_pivots(bars: list[Bar], strength: int) -> list[Pivot]:
    """Fractal swing highs/lows: a swing high at i is strictly the max high over
    [i-strength, i+strength]. A pivot is only *confirmed* (usable) `strength`
    bars later, which the engine must respect to stay causal."""
    pivots: list[Pivot] = []
    n = len(bars)
    for i in range(strength, n - strength):
        hi = bars[i].high
        lo = bars[i].low
        window = range(i - strength, i + strength + 1)
        if all(bars[j].high <= hi for j in window) and any(bars[j].high < hi for j in window if j != i):
            pivots.append(Pivot(i, hi, "high", i + strength))
        if all(bars[j].low >= lo for j in window) and any(bars[j].low > lo for j in window if j != i):
            pivots.append(Pivot(i, lo, "low", i + strength))
    return pivots


def active_sr_levels(
    pivots: list[Pivot],
    current_index: int,
    lookback_bars: int,
) -> list[tuple[float, str]]:
    """S/R levels known and recent as of `current_index` (causal): a pivot is
    only included once confirmed and within the lookback window."""
    levels: list[tuple[float, str]] = []
    lo_bound = current_index - lookback_bars
    for p in pivots:
        if p.confirmed_at <= current_index and p.index >= lo_bound:
            levels.append((p.price, p.kind))
    return levels


def near_level(price: float, levels: list[tuple[float, str]], proximity: float, kind: str | None = None) -> bool:
    for lvl, lkind in levels:
        if kind is not None and lkind != kind:
            continue
        if abs(price - lvl) <= proximity:
            return True
    return False
