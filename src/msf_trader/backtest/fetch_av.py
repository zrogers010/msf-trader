"""Fetch multi-year intraday equity bars from Alpha Vantage (free API key).

Why this exists: yfinance caps sub-hourly history at ~60 days, so it cannot give
a sample large enough to evaluate the strategy. Alpha Vantage's
TIME_SERIES_INTRADAY supports a `month=YYYY-MM` slice going back ~2 decades, so
we can assemble years of 5-minute SPY and resample to the strategy timeframe.

The course's premise ("all charts act and react the same way") lets us prototype
on SPY and read it as /ES: SPY x 10 ~= the S&P index ~= the /ES chart, so scaling
prices by 10 keeps the point-based spec (e.g. the +3-point target) valid and the
per-contract P&L comparable to one e-mini.

Requires ALPHAVANTAGE_API_KEY. NOTE (2026): Alpha Vantage moved intraday
(TIME_SERIES_INTRADAY, incl. the `month=` history slices) behind its PREMIUM
plan -- the free tier returns a "premium endpoint" notice. This fetcher therefore
needs a premium AV key; for a free intraday source use a different vendor
(Alpaca / Twelve Data / Polygon) + `normalize-data`. See docs/BACKTEST_PLAN.md.
Network/data only; nothing is sent to a broker.
"""
from __future__ import annotations

import csv
import io
import os
import time
import urllib.request
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

_ENDPOINT = "https://www.alphavantage.co/query"


def _recent_months(n: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def _fetch_month(symbol: str, interval: str, month: str, key: str, extended_hours: bool) -> list[tuple]:
    params = (
        f"?function=TIME_SERIES_INTRADAY&symbol={symbol}&interval={interval}"
        f"&month={month}&outputsize=full&datatype=csv&adjusted=false"
        f"&extended_hours={'true' if extended_hours else 'false'}&apikey={key}"
    )
    with urllib.request.urlopen(_ENDPOINT + params, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    if not text.lstrip().lower().startswith("timestamp"):
        # Alpha Vantage returns a JSON note on rate-limit / bad key / bad symbol
        snippet = text.strip().replace("\n", " ")[:300]
        raise RuntimeError(f"Alpha Vantage error for {symbol} {month}: {snippet}")
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        rows.append((r["timestamp"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
    return rows


def fetch_av_csv(
    out_csv: str | Path,
    symbol: str = "SPY",
    api_key: str | None = None,
    months: int = 24,
    interval: str = "5min",
    target_minutes: int = 10,
    price_scale: float = 10.0,
    extended_hours: bool = False,
    tz: str = "America/New_York",
    sleep_seconds: float = 15.0,
) -> Path:
    import pandas as pd
    from dotenv import load_dotenv

    load_dotenv()
    key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        raise RuntimeError(
            "ALPHAVANTAGE_API_KEY not set. Get a free key at "
            "https://www.alphavantage.co/support/#api-key and add it to .env."
        )

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    zone = ZoneInfo(tz)

    all_rows: list[tuple] = []
    month_list = _recent_months(months)
    for idx, ym in enumerate(month_list):
        all_rows.extend(_fetch_month(symbol, interval, ym, key, extended_hours))
        if idx < len(month_list) - 1 and sleep_seconds:
            time.sleep(sleep_seconds)

    if not all_rows:
        raise RuntimeError(f"Alpha Vantage returned no rows for {symbol}.")

    # Build a tz-aware (ET) frame; AV intraday timestamps are US/Eastern, naive.
    ts = pd.to_datetime([r[0] for r in all_rows]).tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
    df = pd.DataFrame(
        {
            "open": [float(r[1]) for r in all_rows],
            "high": [float(r[2]) for r in all_rows],
            "low": [float(r[3]) for r in all_rows],
            "close": [float(r[4]) for r in all_rows],
            "volume": [float(r[5]) for r in all_rows],
        },
        index=ts,
    )
    df = df[df.index.notna()].sort_index()
    df = df[~df.index.duplicated(keep="first")]

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    r = (
        df.resample(f"{target_minutes}min", label="left", closed="left")
        .agg(agg)
        .dropna(subset=["open", "high", "low", "close"])
    )

    with out_csv.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for t, row in r.iterrows():
            fh.write(
                f"{t.isoformat()},{row['open']*price_scale:.2f},{row['high']*price_scale:.2f},"
                f"{row['low']*price_scale:.2f},{row['close']*price_scale:.2f},{float(row['volume'] or 0):.0f}\n"
            )
    return out_csv
