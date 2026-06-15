"""Fetch REAL /ES futures bars from Databento (GLBX.MDP3 / CME Globex).

This is the gold-standard source: the actual e-mini contract, with the overnight
(Globex) session intact, so gap / prior-close levels behave the way the course
teaches (unlike an RTH-only SPY proxy). We pull the **continuous front-month**
(`ES.c.0`, volume-rolled) so there are no per-contract roll gaps in the series.

Configurable timeframe: fetches `ohlcv-1m` (or `ohlcv-1h` for >=60-min targets)
and resamples to `target_minutes`. No ×10 scaling needed -- /ES already trades at
index points, so the point-based spec applies directly.

Cost: OHLCV records are tiny; a couple of years easily fits Databento's free
signup credit. Requires DATABENTO_API_KEY in .env. Network/data only.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def fetch_es_databento(
    out_csv: str | Path,
    symbol: str = "ES.c.0",
    months: int = 24,
    target_minutes: int = 10,
    dataset: str = "GLBX.MDP3",
    tz: str = "America/New_York",
    api_key: str | None = None,
) -> Path:
    import databento as db
    from dotenv import load_dotenv

    load_dotenv()
    key = api_key or os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError(
            "DATABENTO_API_KEY not set. Add it to .env (free signup with credit at "
            "https://databento.com/)."
        )

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    zone = ZoneInfo(tz)

    end = datetime.now(timezone.utc)
    base_schema = "ohlcv-1h" if (target_minutes >= 60 and target_minutes % 60 == 0) else "ohlcv-1m"

    client = db.Historical(key)

    # Clamp `end` to what the dataset actually has (avoids 422 data_end_after_available_end).
    try:
        rng = client.metadata.get_dataset_range(dataset=dataset)
        avail_end_raw = rng.get("end") if isinstance(rng, dict) else None
        if avail_end_raw is not None:
            avail_end = datetime.fromisoformat(str(avail_end_raw).replace("Z", "+00:00"))
            if avail_end.tzinfo is None:
                avail_end = avail_end.replace(tzinfo=timezone.utc)
            end = min(end, avail_end)
    except Exception:
        end = end - timedelta(hours=1)  # conservative fallback
    start = end - timedelta(days=int(months * 30.5))
    data = client.timeseries.get_range(
        dataset=dataset,
        symbols=symbol,
        stype_in="continuous",
        schema=base_schema,
        start=start,
        end=end,
    )
    df = data.to_df(price_type="float")  # tz-aware UTC index, float dollar prices
    if df.empty:
        raise RuntimeError(f"Databento returned no rows for {symbol} ({base_schema}).")

    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = df.index.tz_convert(zone)

    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    r = (
        df.resample(f"{target_minutes}min", label="left", closed="left")
        .agg(agg)
        .dropna(subset=["open", "high", "low", "close"])
    )

    with out_csv.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for ts, row in r.iterrows():
            fh.write(
                f"{ts.isoformat()},{row['open']:.2f},{row['high']:.2f},"
                f"{row['low']:.2f},{row['close']:.2f},{float(row['volume'] or 0):.0f}\n"
            )
    return out_csv
