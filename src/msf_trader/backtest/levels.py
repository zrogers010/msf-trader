"""Multi-source support/resistance ZONE model.

This is the course-faithful replacement for a single fractal-pivot level. Per
docs/STRATEGY_RULES_REVIEWED.md §2, a level is a *zone* (an approximation, not an
exact tick) at a price where the market reacted, and it is "more important" when
several independent sources agree there and/or price repeatedly tested it.

Sources combined here (each a distinct "reason" for confluence scoring):
  - confirmed fractal swing pivots               [Module3-9]
  - prior-day high / low / close                 [Module2-4 @ 18:00; Module3-6 @ 07:04]
  - gap window (today's open) & gap fill (prior close)  [Module3-13 @ 02:30]
  - moving averages as dynamic S/R (20/50/200)   [Module3-8 @ 03:40]
  - round-number levels                          (common practice; ASSUMPTION)

Everything is CAUSAL: only information available at or before bar index `i` is
used (pivots only after they confirm; prior-day levels from completed days; the
day's open only once the first bar of the day has printed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .data import Bar
from .indicators import Pivot


@dataclass
class Zone:
    """A clustered S/R band. `sources` lists the distinct reasons present."""
    lo: float
    hi: float
    kind: str                 # "support" | "resistance" | "both"
    sources: tuple[str, ...]
    touches: int = 0

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0

    @property
    def n_sources(self) -> int:
        # MA periods (ma20/ma50/ma200) count as one "moving average" reason.
        norm = {("ma" if s.startswith("ma") else s) for s in self.sources}
        return len(norm)

    @property
    def score(self) -> int:
        """Confluence-ish strength: distinct sources + capped touch count."""
        return self.n_sources + min(self.touches, 3)

    def supports(self) -> bool:
        return self.kind in ("support", "both")

    def resists(self) -> bool:
        return self.kind in ("resistance", "both")

    def near(self, price: float, proximity: float) -> bool:
        return (self.lo - proximity) <= price <= (self.hi + proximity)


def candidate_levels(
    bars: list[Bar],
    i: int,
    spec,
    pivots: list[Pivot],
    pday: dict,
    mas: dict[int, list[float | None]],
    day_open: dict[date, float],
) -> list[tuple[float, str, str]]:
    """Return (price, kind, source) candidates known causally as of bar `i`."""
    out: list[tuple[float, str, str]] = []
    cur = bars[i]

    # 1) confirmed fractal pivots within the lookback window
    lo_bound = i - spec.sr_lookback_bars
    for p in pivots:
        if p.confirmed_at <= i and p.index >= lo_bound:
            out.append((p.price, "resistance" if p.kind == "high" else "support", "pivot"))

    # 2) prior-day high / low / close
    pd = pday.get(cur.date)
    if pd is not None:
        if spec.use_prior_day_levels:
            out.append((pd["high"], "resistance", "prior_day_high"))
            out.append((pd["low"], "support", "prior_day_low"))
            out.append((pd["close"], "both", "prior_day_close"))
        # 3) gap window (today's open). The gap-FILL level is the prior close,
        # which prior_day_close already contributes; only add it here if prior-day
        # levels are disabled, so we never double-count the same price as two
        # separate confluence "reasons".
        if spec.use_gap_levels:
            o = day_open.get(cur.date)
            if o is not None:
                out.append((o, "both", "gap_window"))
            if not spec.use_prior_day_levels:
                out.append((pd["close"], "both", "gap_fill"))

    # 4) moving averages as dynamic S/R
    if spec.use_ma_levels:
        for period in spec.ma_periods:
            v = mas.get(period, [None] * len(bars))[i]
            if v is not None:
                out.append((v, "both", f"ma{period}"))

    # 5) round numbers near current price
    if spec.use_round_numbers and spec.round_number_step > 0:
        step = spec.round_number_step
        nearest = round(cur.close / step) * step
        out.append((float(nearest), "both", "round"))

    return out


def _merge_kind(a: str, b: str) -> str:
    return a if a == b else "both"


def cluster_into_zones(cands: list[tuple[float, str, str]], width: float) -> list[Zone]:
    """Greedy-cluster candidate prices within `width` into zones."""
    if not cands:
        return []
    ordered = sorted(cands, key=lambda c: c[0])
    zones: list[Zone] = []
    lo = hi = ordered[0][0]
    kind = ordered[0][1]
    sources = [ordered[0][2]]
    for price, k, src in ordered[1:]:
        if price - lo <= width:
            hi = price
            kind = _merge_kind(kind, k)
            sources.append(src)
        else:
            zones.append(Zone(lo, hi, kind, tuple(sources)))
            lo = hi = price
            kind = k
            sources = [src]
    zones.append(Zone(lo, hi, kind, tuple(sources)))
    return zones


def count_touches(bars: list[Bar], i: int, zone: Zone, lookback: int, proximity: float) -> int:
    """How many recent bars (in [i-lookback, i]) reached into the zone band."""
    start = max(0, i - lookback)
    touches = 0
    for j in range(start, i + 1):
        b = bars[j]
        if b.low <= zone.hi + proximity and b.high >= zone.lo - proximity:
            touches += 1
    return touches


def active_zones(
    bars: list[Bar],
    i: int,
    spec,
    pivots: list[Pivot],
    pday: dict,
    mas: dict[int, list[float | None]],
    day_open: dict[date, float],
) -> list[Zone]:
    """Causal list of scored S/R zones as of bar `i` (score >= spec.min_zone_score)."""
    cands = candidate_levels(bars, i, spec, pivots, pday, mas, day_open)
    zones = cluster_into_zones(cands, spec.zone_width_points)
    out: list[Zone] = []
    for z in zones:
        z.touches = count_touches(bars, i, z, spec.zone_touch_lookback, spec.sr_proximity_points)
        if z.score >= spec.min_zone_score:
            out.append(z)
    return out


def nearest_zone(zones: list[Zone], price: float, proximity: float, want: str) -> Zone | None:
    """Nearest zone to `price` that can act as `want` ('support'|'resistance')."""
    best: Zone | None = None
    best_d = float("inf")
    for z in zones:
        if want == "support" and not z.supports():
            continue
        if want == "resistance" and not z.resists():
            continue
        if not z.near(price, proximity):
            continue
        d = abs(price - z.mid)
        if d < best_d:
            best_d = d
            best = z
    return best


def opposing_zone(
    zones: list[Zone],
    entry: float,
    direction: str,
    min_pts: float,
    max_pts: float,
) -> tuple[float, Zone] | None:
    """Nearest zone in the PROFIT direction = the structural first target.

    Per docs/TRADING_ESSENCE.md §7(c): the target is "the next opposing level",
    not a fixed number of points. For a long we look for the nearest resistance
    ABOVE entry; for a short, the nearest support BELOW. We aim at the zone's
    NEAR edge (price reacts on first touch), and only accept targets in
    [min_pts, max_pts] away. Returns (target_price, zone) or None.
    """
    best: tuple[float, Zone] | None = None
    best_d = float("inf")
    for z in zones:
        if direction == "long":
            if not z.resists():
                continue
            edge = z.lo                 # near side, approached from below
            d = edge - entry
        else:
            if not z.supports():
                continue
            edge = z.hi                 # near side, approached from above
            d = entry - edge
        if d < min_pts or d > max_pts:
            continue
        if d < best_d:
            best_d = d
            best = (edge, z)
    return best


def build_day_open(bars: list[Bar]) -> dict[date, float]:
    """First-seen open per date (the day's opening print)."""
    out: dict[date, float] = {}
    for b in bars:
        if b.date not in out:
            out[b.date] = b.open
    return out
