"""Parameter-sensitivity sweep.

Re-runs the backtest across a grid of the ASSUMPTION parameters (the values the
course never specified) so we can see how fragile the results are. A strategy
whose P&L swings wildly with the S/R definition or trail rule is telling you the
"edge" is mostly in the assumptions, not the course.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import product

from .data import Bar
from .engine import run_backtest
from .spec import StrategySpec


def sweep(bars: list[Bar], base: StrategySpec, grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    rows: list[dict] = []
    for combo in product(*[grid[k] for k in keys]):
        overrides = dict(zip(keys, combo))
        spec = replace(base, **overrides)
        m = run_backtest(bars, spec).metrics()
        rows.append(
            {
                **overrides,
                "trades": m["trades"],
                "win_rate": round(m["win_rate"], 4),
                "net_pnl": round(m["net_pnl"], 2),
                "profit_factor": (round(m["profit_factor"], 3) if m["profit_factor"] != float("inf") else None),
                "max_drawdown": round(m["max_drawdown"], 2),
            }
        )
    return rows


# Legacy-mode grid (single fractal pivot). Kept for comparison.
LEGACY_GRID: dict[str, list] = {
    "sr_pivot_strength": [2, 3, 4],
    "sr_proximity_points": [1.0, 2.0, 3.0],
    "min_move_points": [2.0, 3.0, 5.0],
    "runner_trail": ["prev_bar_extreme", "fixed_points"],
}

# Confluence-mode grid: the ASSUMPTION knobs of the course-faithful model. If P&L
# is robust across the confluence threshold and zone width, the edge is in the
# method, not a single tuned parameter.
DEFAULT_GRID: dict[str, list] = {
    "min_confluence_factors": [3, 4],
    "retracement_fraction": [0.382, 0.5],
    "max_stop_points": [3.0, 5.0, 8.0, 1e9],   # 1e9 ~= filter disabled (for comparison)
    "zone_width_points": [1.0, 2.0],
}
