"""Swing (multi-day) strategies that survived honest out-of-sample validation.

See docs/SWING_STRATEGY.md and docs/EQUITY_STRATEGY_RESEARCH.md for the research
trail. The headline strategy is RSI(2) mean-reversion (`rsi2`).
"""
from .rsi2 import (
    Rsi2Params,
    DEFAULT_UNIVERSE,
    wilder_rsi,
    rsi2_trades,
    portfolio_backtest,
    PortfolioResult,
)
from .signals import Position, PlannedOrder, compute_daily_plan
from .broker import PaperBroker, RobinhoodMCPBroker
from .runner import run_daily

__all__ = [
    "Rsi2Params",
    "DEFAULT_UNIVERSE",
    "wilder_rsi",
    "rsi2_trades",
    "portfolio_backtest",
    "PortfolioResult",
    "Position",
    "PlannedOrder",
    "compute_daily_plan",
    "PaperBroker",
    "RobinhoodMCPBroker",
    "run_daily",
]
