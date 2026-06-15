"""Trigger candles and approach classification.

Per docs/STRATEGY_RULES_REVIEWED.md §3-§4:
  - Triggers: doji / pseudo-doji, tail candle (body in upper/lower third),
    exhaustion candle (large range + heavy volume + tail).  [Module3-2/3-3/3-4]
  - Approach: HOW price reached the level decides fade vs breakout. Price coming
    "straight into" a level -> fade (reversal); price "consolidating over/at" a
    level -> building energy to break through -> do not fade.  [Module3-13]

All functions are causal (use bars up to index `i`).
"""
from __future__ import annotations

from .data import Bar


def is_doji(bar: Bar, frac: float) -> bool:
    """Open ~ close: body <= frac * range. Covers pseudo-doji (narrow body)."""
    rng = bar.high - bar.low
    if rng <= 0:
        return False
    return abs(bar.close - bar.open) <= frac * rng


def tail_signal(bar: Bar, third: float) -> str | None:
    """Tail candle: body sits in the upper or lower `third` of the range.

    Body in the UPPER third (long lower tail, sellers rejected) -> "bullish".
    Body in the LOWER third (long upper tail, buyers rejected)  -> "bearish".
    """
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    body_top = max(bar.open, bar.close)
    body_bot = min(bar.open, bar.close)
    # body fully in the upper third
    if body_bot >= bar.low + (1.0 - third) * rng:
        return "bullish"
    # body fully in the lower third
    if body_top <= bar.low + third * rng:
        return "bearish"
    return None


def exhaustion_signal(bars: list[Bar], i: int, spec, vol_avg: float | None) -> str | None:
    """Large-range candle on heavy volume with a tail = exhaustion of the move.

    Direction follows the tail (rejection side). Needs a recent volume average;
    if volume data is absent (vol_avg falsy) volume is not required.
    """
    bar = bars[i]
    rng = bar.high - bar.low
    if rng <= 0:
        return None
    # large range vs recent bars
    start = max(0, i - spec.volume_lookback)
    recent = bars[start:i] or [bar]
    avg_rng = sum((b.high - b.low) for b in recent) / len(recent)
    if avg_rng <= 0 or rng < spec.exhaustion_range_mult * avg_rng:
        return None
    # heavy volume (only enforced if we have volume info)
    if vol_avg:
        if bar.volume < spec.volume_spike_mult * vol_avg:
            return None
    return tail_signal(bar, spec.tail_body_third)


def volume_spike(bars: list[Bar], i: int, spec, vol_avg: float | None) -> bool:
    if not vol_avg:
        return False
    return bars[i].volume >= spec.volume_spike_mult * vol_avg


def avg_volume(bars: list[Bar], i: int, lookback: int) -> float | None:
    start = max(0, i - lookback)
    window = bars[start:i]
    vols = [b.volume for b in window if b.volume]
    if not vols:
        return None
    return sum(vols) / len(vols)


def reversal_direction(bars: list[Bar], i: int, spec, vol_avg: float | None) -> dict:
    """Which reversal triggers are present at bar i and their directions.

    Returns {"doji": bool, "tail": "bullish"|"bearish"|None,
             "exhaustion": "bullish"|"bearish"|None}.
    A doji is direction-neutral; the engine assigns direction from the move/level.
    """
    bar = bars[i]
    return {
        "doji": is_doji(bar, spec.doji_body_fraction),
        "tail": tail_signal(bar, spec.tail_body_third),
        "exhaustion": exhaustion_signal(bars, i, spec, vol_avg),
    }


def has_bullish_trigger(trig: dict) -> bool:
    return trig["doji"] or trig["tail"] == "bullish" or trig["exhaustion"] == "bullish"


def has_bearish_trigger(trig: dict) -> bool:
    return trig["doji"] or trig["tail"] == "bearish" or trig["exhaustion"] == "bearish"


def move_length(bars: list[Bar], i: int, direction: str, lookback: int) -> int:
    """Candle count of the move into bar `i` (docs/TRADING_ESSENCE.md §3).

    For a SHORT setup the prior move is UP, so we anchor at the lowest low in the
    lookback window and count candles to `i`. For a LONG setup the prior move is
    DOWN, so we anchor at the highest high. This mirrors the course's "how many
    candles did the market go up into the resistance, counting from the most
    recent pivot low" [Module4-1 @ 03:49]."""
    start = max(0, i - lookback)
    rng = range(start, i + 1)
    if direction == "up":          # rose into resistance (short setup)
        anchor = min(rng, key=lambda j: bars[j].low)
    else:                          # fell into support (long setup)
        anchor = max(rng, key=lambda j: bars[j].high)
    return i - anchor


def approach_type(bars: list[Bar], i: int, zone, spec) -> str:
    """'consolidating' if price spent >= consolidation_min_bars near the zone in
    the lookback window (building energy to break through -> don't fade), else
    'straight_in' (impulsive arrival -> fadeable reversal)."""
    start = max(0, i - spec.approach_lookback)
    near = 0
    for j in range(start, i):  # bars BEFORE the signal bar
        if zone.near(bars[j].close, spec.sr_proximity_points):
            near += 1
    return "consolidating" if near >= spec.consolidation_min_bars else "straight_in"
