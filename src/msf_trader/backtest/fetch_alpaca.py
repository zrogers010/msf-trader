"""Fetch multi-year intraday equity bars from Alpaca Market Data (free tier).

Alpaca's free data plan serves historical bars from the IEX feed going back
several years, at arbitrary minute timeframes (so we can pull 10-minute directly,
no resampling). This is the free path to a sample large enough to actually
evaluate the strategy.

Read SPY as a /ES-equivalent: SPY x 10 ~= the S&P index ~= the /ES chart, so the
point-based spec (e.g. the +3-point target) stays valid and per-contract P&L is
comparable to one e-mini.

Caveats (see docs/BACKTEST_PLAN.md):
  - free feed is IEX-only -> partial volume (price OHLC are fine; volume-based
    confluence is noisier);
  - SPY is RTH-only (no overnight session), so gap/prior-close levels differ from
    real /ES. Use split/dividend adjustment to avoid ex-div gaps.

Credentials: APCA_API_KEY_ID + APCA_API_SECRET_KEY (or ALPACA_API_KEY_ID /
ALPACA_API_SECRET_KEY) in .env. Free signup, no funding required.
Network/data only; nothing is sent to a broker.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_BARS_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"


def _credentials(key_id: str | None, secret: str | None) -> tuple[str, str]:
    from dotenv import load_dotenv

    load_dotenv()
    key_id = key_id or os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY_ID")
    secret = secret or os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET_KEY")
    if not key_id or not secret:
        raise RuntimeError(
            "Alpaca credentials not set. Add APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "to .env (free key+secret at https://alpaca.markets/, no funding needed)."
        )
    return key_id, secret


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_alpaca_csv(
    out_csv: str | Path,
    symbol: str = "SPY",
    months: int = 24,
    target_minutes: int = 10,
    price_scale: float = 10.0,
    feed: str = "iex",
    adjustment: str = "all",
    tz: str = "America/New_York",
    key_id: str | None = None,
    secret: str | None = None,
) -> Path:
    key_id, secret = _credentials(key_id, secret)
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    zone = ZoneInfo(tz)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(months * 30.5))
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    base_q = {
        "timeframe": f"{target_minutes}Min",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": "10000",
        "adjustment": adjustment,
        "feed": feed,
        "sort": "asc",
    }
    url_base = _BARS_URL.format(symbol=symbol)

    s = price_scale
    written = 0
    page_token: str | None = None
    with out_csv.open("w", newline="") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        while True:
            q = dict(base_q)
            if page_token:
                q["page_token"] = page_token
            data = _get_json(url_base + "?" + urllib.parse.urlencode(q), headers)
            for b in data.get("bars") or []:
                ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(zone)
                fh.write(
                    f"{ts.isoformat()},{b['o']*s:.2f},{b['h']*s:.2f},"
                    f"{b['l']*s:.2f},{b['c']*s:.2f},{float(b.get('v') or 0):.0f}\n"
                )
                written += 1
            page_token = data.get("next_page_token")
            if not page_token:
                break

    if written == 0:
        raise RuntimeError(
            f"Alpaca returned no bars for {symbol} (feed={feed}, {base_q['start']}..{base_q['end']}). "
            "Check the symbol and that your plan allows this feed."
        )
    return out_csv
