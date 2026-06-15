"""Phase 2: backtesting the extracted strategy.

This package turns the human-reviewed rules (docs/STRATEGY_RULES_REVIEWED.md) into
an executable, parameterized strategy. CONFIRMED course rules and ASSUMPTIONS
(parameters the course never specified) are tracked separately so a backtest can
never silently treat an assumption as a course fact.

No live trading or order execution lives here -- simulation only.
"""
