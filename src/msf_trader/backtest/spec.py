"""Executable strategy specification derived from docs/STRATEGY_RULES_REVIEWED.md.

Each parameter is tagged in PROVENANCE as either:
  - "confirmed": explicit in the course (with citation)
  - "assumption": the course did not specify it; we chose a documented default
    that the backtest will treat as a tunable assumption, NOT a course fact.

The three open items from the review become explicit ASSUMPTIONS here:
  1. session clock times / timezone
  2. runner trailing-stop rule
  3. algorithmic support/resistance definition
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Provenance(str, Enum):
    confirmed = "confirmed"
    assumption = "assumption"


@dataclass(frozen=True)
class SessionWindow:
    name: str
    start: str  # "HH:MM" in the spec timezone
    end: str
    allow_long: bool = True
    allow_short: bool = True


@dataclass
class StrategySpec:
    # --- Instrument / timeframe (CONFIRMED) ------------------------------
    instrument: str = "ES"
    timeframe_minutes: int = 10
    timezone: str = "America/New_York"
    point_value: float = 50.0      # /ES = $50 per index point
    tick_size: float = 0.25        # $12.50 per tick

    # --- Moving averages (CONFIRMED) -------------------------------------
    ma_periods: tuple[int, ...] = (20, 50, 200)

    # --- Strategy mode ---------------------------------------------------
    # "essence"    = the geometry from docs/TRADING_ESSENCE.md: extreme-entry +
    #                TINY structural stop + STRUCTURAL target (next opposing zone)
    #                + reward:risk gate + candle-count maturity. This is the most
    #                course-faithful mode and supersedes "confluence".
    # "confluence" = zone levels + confluence gate + approach type + retracement
    #                entry, but with a FIXED +N first target (the prior attempt).
    # "legacy"     = original single fractal-pivot + next-open detector (kept for
    #                comparison and the mechanics unit tests).
    strategy_mode: str = "confluence"

    # --- Sessions: STRUCTURE confirmed, CLOCK TIMES are an ASSUMPTION ----
    # Course: trade first 60-90 min (open) and last 60-90 min (close); midday
    # = no shorts. Clock times below assume US equity RTH (ET).
    sessions: tuple[SessionWindow, ...] = (
        SessionWindow("open", "09:30", "11:00", allow_long=True, allow_short=True),
        SessionWindow("midday", "11:00", "14:30", allow_long=True, allow_short=False),
        SessionWindow("close", "14:30", "16:00", allow_long=True, allow_short=True),
    )

    # --- Entry (CONFIRMED structure; detector thresholds are ASSUMPTION) -
    move_lookback_bars: int = 10          # ~10-candle directional move before reversal
    require_reversal_at_sr: bool = True    # reversal candle must form at an S/R level
    doji_body_fraction: float = 0.10       # body <= frac*range counts as a doji
    min_move_points: float = 3.0           # min net move over lookback to call it a "trend"
    enter_on_next_open: bool = True        # signal at bar close -> fill next bar open

    # --- Support/Resistance detection (ASSUMPTION) -----------------------
    sr_pivot_strength: int = 3             # fractal: N bars on each side define a swing
    sr_lookback_bars: int = 60             # how far back to keep S/R levels
    sr_proximity_points: float = 2.0       # price must be within this of a level
    use_prior_day_levels: bool = True      # include prior-day high/low/close as S/R

    # --- Level/zone model (confluence mode; encodes RULES §2, ASSUMPTION knobs)
    zone_width_points: float = 2.0         # candidates within this merge into a zone
    use_gap_levels: bool = True            # gap window (today open) + gap fill (prior close)
    use_ma_levels: bool = True             # 20/50/200 MAs as dynamic S/R
    use_round_numbers: bool = True         # round-number levels
    round_number_step: float = 5.0         # /ES round-number spacing (points)
    zone_touch_lookback: int = 60          # window for counting zone touches
    min_zone_score: int = 1                # keep zones at/above this strength

    # --- Confluence gate (RULES §5; threshold is an ASSUMPTION encoding) -
    min_confluence_factors: int = 3        # independent reasons required or NO TRADE
    use_volume_confluence: bool = True     # count a volume spike as a reason
    volume_lookback: int = 20
    volume_spike_mult: float = 1.5
    exhaustion_range_mult: float = 1.5     # exhaustion candle range vs recent avg

    # --- Trigger candles (RULES §4) --------------------------------------
    tail_body_third: float = 1.0 / 3.0     # tail = body within upper/lower third

    # --- Approach type (RULES §3) ----------------------------------------
    approach_lookback: int = 6             # bars before the signal to inspect
    consolidation_min_bars: int = 3        # >= this near the zone => "consolidating" (skip fade)

    # --- Entry style (RULES §6) ------------------------------------------
    entry_mode: str = "retracement"        # "retracement" | "next_open"
    retracement_fraction: float = 0.5      # ~50% pullback into the signal candle
    entry_valid_bars: int = 3              # limit order good-till-N bars, else cancel

    # --- Essence geometry (docs/TRADING_ESSENCE.md §3, §7) ---------------
    # Used when strategy_mode == "essence".
    target_mode: str = "fixed"             # "structural" (next opposing zone) | "fixed"
    min_reward_risk: float = 1.5           # skip unless target_dist/stop_dist >= this
    target_min_points: float = 2.0         # ignore opposing zones nearer than this
    target_max_points: float = 40.0        # search window for the opposing zone
    min_move_candles: int = 5              # candle-count maturity of the move into the level

    # --- Stop-loss (CONFIRMED) -------------------------------------------
    # Stop = a completed bar CLOSE beyond the far side of the signal candle.
    stop_buffer_ticks: int = 0             # optional extra buffer beyond the level
    # CONFIRMED rule: skip the setup if the structural stop is too far (risk too
    # large). The distance THRESHOLD is an ASSUMPTION. 0 disables the filter.
    max_stop_points: float = 8.0           # skip if entry->stop distance exceeds this
    # Stop EXECUTION model (ASSUMPTION; affects realized loss size, not the rule):
    #   "close" = course-literal: exit only when a bar CLOSES beyond the stop, at
    #             that close (avoids wicks, but realized loss can far exceed the
    #             structural risk on a big bar).
    #   "touch" = resting STOP ORDER at the level: exit intrabar at the stop price
    #             (caps realized loss near structural risk, but eats wick whipsaws).
    stop_exec: str = "close"

    # --- Target / scale-out (CONFIRMED target; runner trail = ASSUMPTION)-
    contracts: int = 2                     # need >1 to scale; example uses 2
    first_target_points: float = 3.0       # 1st contract exits at +3 (CONFIRMED)
    move_to_breakeven_after_first_target: bool = True   # CONFIRMED
    # Runner trailing rule (ASSUMPTION): ratchet stop to the extreme of the
    # previous completed bar, only in the favorable direction.
    runner_trail: str = "prev_bar_extreme"  # one of: prev_bar_extreme | fixed_points | none
    runner_trail_points: float = 3.0        # used when runner_trail == "fixed_points"
    runner_never_negative: bool = True      # once at breakeven, never let it turn negative
    # Looseness for prev_bar_extreme trail (ASSUMPTION): trail off the extreme of
    # the last N completed bars (>1 = looser), plus a points buffer beyond it.
    # Larger values give runners room to breathe instead of ratcheting out.
    runner_trail_lookback: int = 1
    runner_trail_buffer_points: float = 0.0

    # --- Risk (CONFIRMED) ------------------------------------------------
    daily_max_loss_dollars: float = 300.0   # example; user sets their own
    max_concurrent_trades: int = 1

    # --- Costs (BACKTEST_PLAN, ASSUMPTION) -------------------------------
    commission_per_contract: float = 2.50   # round-turn estimate; tune
    slippage_ticks: int = 1                 # adverse slippage on MARKET fills (stop/eod/trail)
    # Adverse slippage on LIMIT fills (retracement entry + profit target). Real
    # limit fills aren't free/perfect; 0 = idealized (kept for deterministic tests).
    limit_slippage_ticks: int = 0

    def per_point(self) -> float:
        return self.point_value


# Field-level provenance + citations. Keys map to StrategySpec attributes.
PROVENANCE: dict[str, tuple[Provenance, str]] = {
    "instrument": (Provenance.confirmed, "STRATEGY_RULES_REVIEWED: Instrument"),
    "timeframe_minutes": (Provenance.confirmed, "Module3-1 @ 01:47; Module5-3 @ 07:51"),
    "ma_periods": (Provenance.confirmed, "Module3-1 @ 01:08, 01:47"),
    "sessions.structure": (Provenance.confirmed, "Module2-4 @ 06:11, 07:19, 08:18, 09:11"),
    "sessions.clock_times": (Provenance.assumption, "RTH 9:30-16:00 ET assumed; not stated verbatim"),
    "move_lookback_bars": (Provenance.confirmed, "Module4-1 @ 03:49 (~10-candle move)"),
    "require_reversal_at_sr": (Provenance.confirmed, "Module4-1 @ 03:49, 04:30"),
    "doji_body_fraction": (Provenance.assumption, "doji threshold; course shows but doesn't quantify"),
    "min_move_points": (Provenance.assumption, "trend-size threshold; not quantified in course"),
    "strategy_mode": (Provenance.assumption, "engine variant selector; not a course concept"),
    "sr_pivot_strength": (Provenance.assumption, "no algorithmic S/R definition in course"),
    "sr_lookback_bars": (Provenance.assumption, "no algorithmic S/R definition in course"),
    "sr_proximity_points": (Provenance.assumption, "no algorithmic S/R definition in course"),
    # Zone model: the SOURCES are CONFIRMED by the course; the numeric knobs are ASSUMPTIONS.
    "use_prior_day_levels": (Provenance.confirmed, "Module2-4 @ 18:00; Module3-6 @ 07:04 (prior H/L/close)"),
    "use_gap_levels": (Provenance.confirmed, "Module3-13 @ 02:30 (gap window/fill levels)"),
    "use_ma_levels": (Provenance.confirmed, "Module3-8 @ 03:40; Module4-1 @ 14:56 (MA as dynamic S/R)"),
    "use_round_numbers": (Provenance.assumption, "common practice; not explicit in course"),
    "zone_width_points": (Provenance.assumption, "zone clustering width; course says 'zone, not exact'"),
    "round_number_step": (Provenance.assumption, "round-number spacing; modeling choice"),
    "zone_touch_lookback": (Provenance.assumption, "touch-count window; modeling choice"),
    "min_zone_score": (Provenance.assumption, "zone-strength filter; modeling choice"),
    # Confluence: the RULE is CONFIRMED; the numeric threshold is an ASSUMPTION encoding.
    "min_confluence_factors": (Provenance.confirmed, "Module3-4 @ 01:09; Module4-1 @ 11:37 (>=2 reasons; count is our encoding)"),
    "use_volume_confluence": (Provenance.confirmed, "Module3-3 @ 02:00 (volume is a factor)"),
    "volume_spike_mult": (Provenance.assumption, "volume-spike threshold; modeling choice"),
    "exhaustion_range_mult": (Provenance.assumption, "exhaustion range threshold; modeling choice"),
    "tail_body_third": (Provenance.confirmed, "Module3-4 @ 00:41 (body in upper/lower third)"),
    "approach_lookback": (Provenance.assumption, "approach window; modeling choice"),
    "consolidation_min_bars": (Provenance.confirmed, "Module3-13 @ 04:59 (consolidate-over => no fade; count is our encoding)"),
    "entry_mode": (Provenance.confirmed, "Module3-4 @ 06:33; Module6-1 @ 18:51 (enter on retracement)"),
    "retracement_fraction": (Provenance.confirmed, "Module6-1 @ 18:51 (~50%); Module6-2 @ 05:31 (.618)"),
    "entry_valid_bars": (Provenance.assumption, "limit good-till window; modeling choice"),
    "target_mode": (Provenance.confirmed, "Module6-1 @ 04:42 (target = next chart level, not fixed); Module4-1 @ 06:00 (gap-fill target)"),
    "min_reward_risk": (Provenance.confirmed, "Module4-1 @ 09:13 (good risk-reward); threshold is our encoding"),
    "target_min_points": (Provenance.assumption, "min structural-target distance; modeling choice"),
    "target_max_points": (Provenance.assumption, "structural-target search window; modeling choice"),
    "min_move_candles": (Provenance.confirmed, "Module4-1 @ 03:26; Module2-3 @ 11:52 (6-7/10 candle move); count is our encoding)"),
    "stop_buffer_ticks": (Provenance.confirmed, "Module5-3 @ 07:51; Module4-1 @ 08:51 (close beyond signal candle)"),
    "max_stop_points": (Provenance.confirmed, "Module4-1/Module6-1: skip if stop too far (threshold is our encoding)"),
    "first_target_points": (Provenance.confirmed, "Module6-1 @ 04:47, 05:07, 05:54 (+3)"),
    "move_to_breakeven_after_first_target": (Provenance.confirmed, "Module6-1 @ 05:15, 06:05"),
    "runner_trail": (Provenance.assumption, "course says 'trail the market' / 'big runner' but no exact rule"),
    "runner_trail_lookback": (Provenance.assumption, "trail looseness; modeling choice"),
    "runner_trail_buffer_points": (Provenance.assumption, "trail room beyond extreme; modeling choice"),
    "runner_never_negative": (Provenance.confirmed, "implied by breakeven stop (Module6-1 @ 05:15)"),
    "stop_exec": (Provenance.assumption, "execution model for the close-beyond stop; modeling choice"),
    "daily_max_loss_dollars": (Provenance.confirmed, "Module6-2 @ 03:18, 11:02 (value is user-set)"),
    "commission_per_contract": (Provenance.assumption, "modeling choice (BACKTEST_PLAN)"),
    "slippage_ticks": (Provenance.assumption, "modeling choice (BACKTEST_PLAN)"),
    "limit_slippage_ticks": (Provenance.assumption, "fill-realism for limit entries/targets (BACKTEST_PLAN)"),
}


def assumptions(spec: StrategySpec | None = None) -> list[str]:
    """Human-readable list of every parameter the backtest is ASSUMING."""
    out = []
    for key, (prov, note) in PROVENANCE.items():
        if prov is Provenance.assumption:
            out.append(f"{key}: {note}")
    return out
