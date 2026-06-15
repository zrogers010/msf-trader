"""Market-data loading for the backtester.

Input is a CSV of OHLCV bars at the strategy timeframe (default 10-minute /ES).
Canonical columns (case-insensitive), header required:
    timestamp, open, high, low, close, volume

The loader is tolerant of common vendor exports:
  - a single timestamp column named timestamp / datetime / date_time, OR
    separate `date` + `time` columns, OR a `date` column that already carries a
    time component;
  - ISO-8601 strings ("2024-01-02T09:30:00-05:00", "2024-01-02 09:30:00") or
    epoch seconds / milliseconds;
  - OHLCV aliases (o/h/l/c, last, vol/v); extra columns are ignored.
Timezone-naive timestamps are localized to the spec timezone; tz-aware ones are
converted to it. For arbitrary vendor files (incl. 1-minute data that needs
resampling or RTH filtering) use `normalize_csv` / the `normalize-data` CLI.

No data is fetched or bundled -- you supply your own /ES history (see
docs/BACKTEST_PLAN.md for sources and caveats).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .spec import SessionWindow, StrategySpec


@dataclass
class Bar:
    ts: datetime          # timezone-aware, in spec timezone
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str | None = None       # e.g. "open" | "midday" | "close" | None
    allow_long: bool = True
    allow_short: bool = True

    @property
    def date(self):
        return self.ts.date()


def _parse_ts(raw: str, tz: ZoneInfo) -> datetime:
    raw = raw.strip()
    # epoch seconds (10 digits) or milliseconds (13 digits)
    digits = raw[:-2] if raw.endswith(".0") else raw
    if digits.isdigit():
        val = int(digits)
        if len(digits) >= 13:
            val //= 1000
        from datetime import timezone
        return datetime.fromtimestamp(val, tz=timezone.utc).astimezone(tz)
    raw = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)  # accepts both "T" and space separators (3.11+)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _resolve_columns(fieldnames) -> dict:
    """Map canonical names -> actual header names, tolerating vendor variants."""
    cols = {(c or "").lower().strip(): c for c in (fieldnames or [])}

    def first(*names):
        return next((cols[n] for n in names if n in cols), None)

    resolved = {
        "ts": first("timestamp", "datetime", "date_time", "date-time"),
        "date": cols.get("date"),
        "time": cols.get("time"),
        "open": first("open", "o"),
        "high": first("high", "h"),
        "low": first("low", "l"),
        "close": first("close", "c", "last"),
        "volume": first("volume", "vol", "v"),
    }
    if resolved["ts"] is None:
        if resolved["date"] is None:
            raise ValueError(
                f"CSV needs a 'timestamp'/'datetime' column or a 'date' (+ optional 'time') "
                f"column. Found: {list(cols)}"
            )
    missing = [k for k in ("open", "high", "low", "close") if resolved[k] is None]
    if missing:
        raise ValueError(f"CSV missing OHLC columns {missing}. Found: {list(cols)}")
    return resolved


def _row_ts(row: dict, rc: dict, tz: ZoneInfo) -> datetime:
    if rc["ts"] is not None:
        return _parse_ts(row[rc["ts"]], tz)
    raw = row[rc["date"]].strip()
    if rc["time"] is not None and row.get(rc["time"]):
        raw = f"{raw} {row[rc['time']].strip()}"
    return _parse_ts(raw, tz)


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def tag_session(dt: datetime, sessions: tuple[SessionWindow, ...]) -> SessionWindow | None:
    t = dt.time()
    for win in sessions:
        start, end = _hhmm(win.start), _hhmm(win.end)
        # half-open [start, end)
        if start <= t < end:
            return win
    return None


def load_bars_csv(path: str | Path, spec: StrategySpec) -> list[Bar]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Bar data not found: {path}")
    tz = ZoneInfo(spec.timezone)

    bars: list[Bar] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        rc = _resolve_columns(reader.fieldnames)
        for row in reader:
            ts = _row_ts(row, rc, tz)
            win = tag_session(ts, spec.sessions)
            vol_raw = row.get(rc["volume"]) if rc["volume"] else None
            bars.append(
                Bar(
                    ts=ts,
                    open=float(row[rc["open"]]),
                    high=float(row[rc["high"]]),
                    low=float(row[rc["low"]]),
                    close=float(row[rc["close"]]),
                    volume=float(vol_raw) if vol_raw not in (None, "") else 0.0,
                    session=win.name if win else None,
                    allow_long=win.allow_long if win else False,
                    allow_short=win.allow_short if win else False,
                )
            )
    bars.sort(key=lambda b: b.ts)
    return bars


def normalize_csv(
    in_path: str | Path,
    out_path: str | Path,
    spec: StrategySpec,
    resample_minutes: int | None = None,
    rth_only: bool = False,
) -> Path:
    """Convert an arbitrary vendor OHLCV CSV into the canonical schema.

    - Resolves vendor column variants (see module docstring).
    - Converts timestamps to the spec timezone.
    - Optionally resamples to `resample_minutes` (e.g. 1-min vendor data -> 10).
    - Optionally keeps only RTH bars (those that fall inside spec.sessions).
    Returns the written path. Uses pandas for resampling only.
    """
    in_path, out_path = Path(in_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bars = load_bars_csv(in_path, spec)

    if resample_minutes:
        import pandas as pd

        idx = pd.DatetimeIndex([b.ts for b in bars])
        df = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            },
            index=idx,
        )
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        df = (
            df.resample(f"{resample_minutes}min", label="left", closed="left")
            .agg(agg)
            .dropna(subset=["open", "high", "low", "close"])
        )
        rows = [
            (ts.to_pydatetime(), r["open"], r["high"], r["low"], r["close"], r["volume"])
            for ts, r in df.iterrows()
        ]
    else:
        rows = [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]

    written = 0
    with out_path.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for ts, o, h, l, c, v in rows:
            if rth_only and tag_session(ts, spec.sessions) is None:
                continue
            fh.write(f"{ts.isoformat()},{o:.2f},{h:.2f},{l:.2f},{c:.2f},{float(v or 0):.0f}\n")
            written += 1
    return out_path


def prior_day_levels(bars: list[Bar]) -> dict:
    """Map each trading date -> {'high','low','close'} of the PRIOR date.
    Useful S/R inputs (an assumption; see spec PROVENANCE)."""
    by_date: dict = {}
    for b in bars:
        d = b.date
        agg = by_date.setdefault(d, {"high": b.high, "low": b.low, "close": b.close})
        agg["high"] = max(agg["high"], b.high)
        agg["low"] = min(agg["low"], b.low)
        agg["close"] = b.close  # last seen for the day

    dates = sorted(by_date)
    out: dict = {}
    for i, d in enumerate(dates):
        if i == 0:
            continue
        out[d] = by_date[dates[i - 1]]
    return out
