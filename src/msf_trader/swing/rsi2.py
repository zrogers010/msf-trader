"""RSI(2) swing mean-reversion — the one strategy that survived validation.

Thesis (and why it works where intraday failed): at a 3-4 day horizon, liquid
equity indices/ETFs *mean-revert* — short-term reversal is one of the most robust
documented anomalies. A deeply oversold dip *inside an uptrend* tends to bounce.
Per-trade moves are ~0.5-1.5%, which dwarfs the ~3 bps round-trip cost wall that
killed every intraday idea we tried (see docs/EQUITY_STRATEGY_RESEARCH.md).

Rules
-----
Entry  : close > 200-day SMA (regime: only buy dips in uptrends)
         AND 2-period Wilder RSI < `rsi_buy` (default 10, deeply oversold)
         -> enter at that day's close.
Exit   : close > `exit_sma`-day SMA (the bounce), or after `max_hold` days.
Sizing : portfolio equal-weights across active positions, capped `max_weight`
         per name (so a single bad trade can't sink the book).

Validation (2004-2026, yfinance daily, 3 bps round-trip cost):
  - Per-instrument OOS (2016+) profit factors 1.1-3.9, 66-75% win, broad across
    SPY/QQQ/SMH/sector ETFs (not one lucky name).
  - 20-ETF capped portfolio: Sharpe ~0.7 in BOTH halves; maxDD ~-20%; crises
    survived (2008 +1%, 2020 +11%, 2022 -2%).

This module is data-vendor agnostic: it reads daily OHLCV CSVs
(`timestamp/date, open, high, low, close, volume`). Fetch with yfinance/Alpaca
(see docs/SWING_STRATEGY.md). Backtest only; not investment advice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Liquid index + sector ETF universe with long history and low single-name tail.
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "DIA", "IWM", "MDY", "SMH", "XLK", "XLF", "XLE", "XLV",
    "XLY", "XLP", "XLI", "XLU", "XLB", "XBI", "GDX", "XME", "EFA", "EEM",
]


@dataclass
class Rsi2Params:
    rsi_buy: float = 10.0          # enter when RSI(2) < this
    rsi_period: int = 2
    regime_sma: int = 200          # only long above this SMA
    exit_sma: int = 5              # exit when close > this SMA
    max_hold: int = 10             # time stop (trading days)
    cost_bps_rt: float = 3.0       # round-trip cost (bps of notional)
    max_weight: float = 0.20       # cap per name in the portfolio


def wilder_rsi(close: pd.Series, n: int = 2) -> pd.Series:
    """Wilder's RSI. Period 2 is the Connors short-term reversal oscillator."""
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1 / n, adjust=False).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _load_daily(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    col = "date" if "date" in df.columns else "timestamp"
    df[col] = pd.to_datetime(df[col])
    df = df.rename(columns={col: "date"}).sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def rsi2_trades(df: pd.DataFrame, p: Rsi2Params = Rsi2Params(), regime: bool = True) -> pd.DataFrame:
    """Non-overlapping single-instrument trades. Returns columns:
    date (entry), exit_date, ret (net of cost, fraction), hold (days)."""
    c = df["close"].reset_index(drop=True)
    dates = df["date"].reset_index(drop=True)
    rsi = wilder_rsi(c, p.rsi_period)
    sma_reg = c.rolling(p.regime_sma).mean()
    sma_ex = c.rolling(p.exit_sma).mean()
    cost = p.cost_bps_rt / 1e4
    n = len(c)
    trades: list[tuple] = []
    i = p.regime_sma
    while i < n - 1:
        in_regime = (not regime) or (c.iloc[i] > sma_reg.iloc[i])
        if in_regime and rsi.iloc[i] < p.rsi_buy:
            entry = c.iloc[i]
            j = i + 1
            while j < n and (j - i) <= p.max_hold and not (c.iloc[j] > sma_ex.iloc[j]):
                j += 1
            j = min(j, n - 1)
            ret = (c.iloc[j] - entry) / entry - cost
            trades.append((dates.iloc[i], dates.iloc[j], ret, j - i))
            i = j + 1
        else:
            i += 1
    return pd.DataFrame(trades, columns=["date", "exit_date", "ret", "hold"])


def _position_returns(df: pd.DataFrame, p: Rsi2Params, regime: bool):
    """Daily close-to-close returns while holding (net of split entry/exit cost),
    plus a 0/1 in-position series. Indexed by date."""
    c = df["close"].reset_index(drop=True)
    dates = pd.to_datetime(df["date"].reset_index(drop=True))
    rsi = wilder_rsi(c, p.rsi_period)
    sma_reg = c.rolling(p.regime_sma).mean()
    sma_ex = c.rolling(p.exit_sma).mean()
    n = len(c)
    pos = np.zeros(n)
    entry_day = np.zeros(n, bool)
    exit_day = np.zeros(n, bool)
    i = p.regime_sma
    while i < n - 1:
        if ((not regime) or c.iloc[i] > sma_reg.iloc[i]) and rsi.iloc[i] < p.rsi_buy:
            j = i + 1
            while j < n and (j - i) <= p.max_hold and not (c.iloc[j] > sma_ex.iloc[j]):
                j += 1
            j = min(j, n - 1)
            pos[i + 1:j + 1] = 1.0
            entry_day[i] = True
            exit_day[j] = True
            i = j + 1
        else:
            i += 1
    ret = c.pct_change().fillna(0.0).values
    cost = p.cost_bps_rt / 1e4
    daily = pos * ret - entry_day * (cost / 2) - exit_day * (cost / 2)
    return pd.Series(daily, index=dates), pd.Series(pos, index=dates)


@dataclass
class PortfolioResult:
    daily: pd.Series                     # daily portfolio returns
    equity: pd.Series                    # cumulative growth of $1
    cagr: float
    sharpe: float
    max_drawdown: float
    exposure: float                      # fraction of days with capital deployed
    return_on_deployed: float            # annualized return per $ actually invested
    by_year: dict[int, float]
    trades_per_instrument: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"CAGR={self.cagr*100:.1f}%  Sharpe={self.sharpe:.2f}  "
            f"maxDD={self.max_drawdown*100:.1f}%  exposure={self.exposure*100:.0f}%  "
            f"ret-on-deployed={self.return_on_deployed*100:.0f}%"
        )


def portfolio_backtest(
    symbols: list[str] | None = None,
    data_dir: str | Path = "data/market",
    suffix: str = "_daily",
    p: Rsi2Params = Rsi2Params(),
    regime: bool = True,
) -> PortfolioResult:
    """Equal-weight (capped) portfolio backtest across `symbols`.

    Reads `{data_dir}/{SYM}{suffix}.csv` daily bars. Capital is split equally
    across the names in a position each day, capped at `p.max_weight` per name
    (excess stays in cash). This bounds single-trade tail risk and gives a
    realistic equity curve with cash drag."""
    symbols = symbols or DEFAULT_UNIVERSE
    data_dir = Path(data_dir)
    rets: dict[str, pd.Series] = {}
    poss: dict[str, pd.Series] = {}
    counts: dict[str, int] = {}
    for s in symbols:
        path = data_dir / f"{s}{suffix}.csv"
        if not path.exists():
            continue
        df = _load_daily(path)
        r, pos = _position_returns(df, p, regime)
        rets[s] = r
        poss[s] = pos
        counts[s] = int(len(rsi2_trades(df, p, regime)))
    if not rets:
        raise FileNotFoundError(
            f"No daily CSVs found in {data_dir} matching {symbols} with suffix {suffix!r}."
        )
    idx = sorted(set().union(*[set(r.index) for r in rets.values()]))
    R = pd.DataFrame(rets).reindex(idx).fillna(0.0)
    P = pd.DataFrame(poss).reindex(idx).fillna(0.0)
    active = P.sum(axis=1)
    per = (1.0 / active.replace(0, np.nan)).clip(upper=p.max_weight)
    W = P.mul(per, axis=0).fillna(0.0)
    daily = (W * R).sum(axis=1)

    equity = (1 + daily).cumprod()
    yrs = (daily.index[-1] - daily.index[0]).days / 365.25
    cagr = equity.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    dd = float((equity / equity.cummax() - 1).min())
    sharpe = daily.mean() / daily.std() * math.sqrt(252) if daily.std() > 0 else float("nan")
    expo = float((daily != 0).mean())
    active_ret = daily[daily != 0]
    roc = float(active_ret.mean() * 252) if len(active_ret) else float("nan")
    by_year = {int(y): float(v) for y, v in ((1 + daily).groupby(daily.index.year).prod() - 1).items()}
    return PortfolioResult(daily, equity, float(cagr), float(sharpe), dd, expo, roc, by_year, counts)
