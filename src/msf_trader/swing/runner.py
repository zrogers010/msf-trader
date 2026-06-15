"""Daily runner for the RSI(2) swing strategy.

Workflow (run ~15 min before the close, once per trading day):
  1. pull fresh daily bars for the universe,
  2. read current positions + buying power from the broker,
  3. compute today's order plan (entries / exits / sizes),
  4. print it for review, and optionally execute it.

Use `--paper` (default) to execute against the local PaperBroker. For live
Robinhood execution, an AI agent with the Robinhood Trading MCP connected reads
the printed plan and calls review_equity_order -> place_equity_order for each
order (or wires `RobinhoodMCPBroker` with its MCP `call_tool`).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .rsi2 import DEFAULT_UNIVERSE, Rsi2Params
from .signals import PlannedOrder, Position, compute_daily_plan
from .broker import AlpacaPaperBroker, PaperBroker


def fetch_daily(symbols: list[str], lookback_days: int = 400) -> dict[str, pd.DataFrame]:
    """Fresh daily bars (split/div adjusted) via yfinance, enough history for the
    200-day regime SMA. Includes today's forming bar so the near-close decision
    uses the latest price."""
    import yfinance as yf

    start = (pd.Timestamp.today() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = yf.download(s, start=start, auto_adjust=True, progress=False)
        if df is None or len(df) == 0:
            continue
        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                "Low": "low", "Close": "close", "Volume": "volume"})
        out[s] = df[["date", "open", "high", "low", "close", "volume"]]
    return out


def load_daily_csvs(symbols: list[str], data_dir: str | Path = "data/market",
                    suffix: str = "_daily") -> dict[str, pd.DataFrame]:
    """Offline alternative to `fetch_daily` — read cached CSVs (for testing)."""
    data_dir = Path(data_dir)
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        path = data_dir / f"{s}{suffix}.csv"
        if path.exists():
            df = pd.read_csv(path)
            col = "date" if "date" in df.columns else "timestamp"
            df = df.rename(columns={col: "date"})
            df["date"] = pd.to_datetime(df["date"])
            out[s] = df.sort_values("date").reset_index(drop=True)
    return out


def run_daily(
    universe: list[str] | None = None,
    params: Rsi2Params = Rsi2Params(),
    target_positions: int = 5,
    broker: str = "paper",
    offline: bool = False,
    state_path: str | Path = "data/paper_account.json",
    asof: date | None = None,
    force: bool = False,
    fixed_notional: float | None = None,
) -> list[PlannedOrder]:
    """Compute (and optionally execute) today's plan.

    broker:
      "dry"          -> compute and print only, no execution (needs a marks source)
      "paper"        -> local PaperBroker simulation (default)
      "alpaca"       -> route to Alpaca's real paper account (paper-api.alpaca.markets)

    For "alpaca", orders are only placed when the market is open (fractional market
    orders require it); pass force=True to override the clock guard.
    """
    universe = universe or DEFAULT_UNIVERSE
    asof = asof or date.today()
    bars = (load_daily_csvs(universe) if offline else fetch_daily(universe))
    marks = {s: float(df["close"].iloc[-1]) for s, df in bars.items()}

    market_open = True
    if broker == "alpaca":
        bk = AlpacaPaperBroker(state_path="data/alpaca_paper_state.json")
        market_open = bk.is_market_open()
        bk.age_positions()
        positions: list[Position] = bk.get_positions()
        equity = bk.get_equity()
        cash = bk.get_cash()
    elif broker == "paper":
        bk = PaperBroker(state_path)
        bk.age_positions()
        positions = bk.get_positions()
        equity = bk.get_equity(marks)
        cash = bk.get_cash()
    else:  # dry run: use a local PaperBroker view for state but never execute
        bk = PaperBroker(state_path)
        positions = bk.get_positions()
        equity = bk.get_equity(marks)
        cash = bk.get_cash()

    plan = compute_daily_plan(asof, bars, positions, equity, cash, params,
                              target_positions, fixed_notional=fixed_notional)

    execute = broker in ("paper", "alpaca") and (market_open or force)
    if execute and plan:
        for order in plan:
            bk.execute(order, asof)
    if broker in ("paper", "alpaca"):
        bk.save()
    return plan
