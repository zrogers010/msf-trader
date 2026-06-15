"""Fetch /ES intraday bars from yfinance for PROTOTYPING ONLY.

yfinance has no native 10-minute interval and limits intraday history (5m is
capped at ~60 days). We download 5-minute `ES=F` and resample to the strategy
timeframe. This is front-month continuous-ish data from Yahoo -- fine for wiring
up and sanity-checking the engine, NOT for production research. Swap in a proper
vendor (see docs/BACKTEST_PLAN.md) for real testing.
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo


def fetch_es_csv(
    out_csv: str | Path,
    period: str = "60d",
    base_interval: str = "5m",
    target_minutes: int = 10,
    symbol: str = "ES=F",
    tz: str = "America/New_York",
    price_scale: float = 1.0,
) -> Path:
    """`price_scale` multiplies OHLC (use 10.0 for SPY -> /ES-equivalent chart)."""
    import yfinance as yf  # lazy import

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    df = yf.download(
        symbol,
        period=period,
        interval=base_interval,
        auto_adjust=False,
        progress=False,
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"yfinance returned no data for {symbol} (period={period}, interval={base_interval}). "
            "Intraday history is limited; try a shorter period or a different symbol."
        )

    # yfinance can return a MultiIndex column frame for single tickers; flatten.
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    # Ensure tz-aware index in target timezone
    idx = df.index
    if idx.tz is None:
        df.index = idx.tz_localize("UTC").tz_convert(ZoneInfo(tz))
    else:
        df.index = idx.tz_convert(ZoneInfo(tz))

    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    resampled = (
        df.resample(f"{target_minutes}min", label="left", closed="left")
        .agg(agg)
        .dropna(subset=["Open", "High", "Low", "Close"])
    )

    s = price_scale
    with out_csv.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for ts, row in resampled.iterrows():
            fh.write(
                f"{ts.isoformat()},{row['Open']*s:.2f},{row['High']*s:.2f},"
                f"{row['Low']*s:.2f},{row['Close']*s:.2f},{float(row['Volume'] or 0):.0f}\n"
            )
    return out_csv
