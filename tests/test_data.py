from datetime import datetime
from zoneinfo import ZoneInfo

from msf_trader.backtest.data import load_bars_csv, prior_day_levels, tag_session
from util import ET, build, make_spec

UTC = ZoneInfo("UTC")


def _dt(h, m):
    return datetime(2026, 6, 1, h, m, tzinfo=ET)


def test_tag_session_windows():
    spec = make_spec()
    s = spec.sessions
    assert tag_session(_dt(9, 35), s).name == "open"
    midday = tag_session(_dt(12, 0), s)
    assert midday.name == "midday" and midday.allow_short is False
    assert tag_session(_dt(15, 0), s).name == "close"
    assert tag_session(_dt(3, 0), s) is None  # overnight


def test_load_bars_csv_tz_and_sessions(tmp_path):
    spec = make_spec()
    csv = tmp_path / "bars.csv"
    # 13:35 UTC == 09:35 ET (EDT) -> open session
    csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-06-01T13:35:00+00:00,100,101,99,100.5,1000\n"
        "2026-06-01T16:00:00+00:00,101,102,100,101.5,800\n"  # 12:00 ET midday
    )
    bars = load_bars_csv(csv, spec)
    assert len(bars) == 2
    assert bars[0].ts.tzinfo is not None
    assert bars[0].ts.astimezone(ET).hour == 9 and bars[0].ts.astimezone(ET).minute == 35
    assert bars[0].session == "open"
    assert bars[1].session == "midday" and bars[1].allow_short is False
    assert bars[0].high == 101.0


def test_load_bars_csv_date_time_columns_and_aliases(tmp_path):
    spec = make_spec()
    csv = tmp_path / "vendor.csv"
    # separate date + time columns, OHLCV aliases, an extra column to ignore
    csv.write_text(
        "Date,Time,o,h,l,c,vol,extra\n"
        "2026-06-01,09:35:00,100,101,99,100.5,1000,foo\n"
        "2026-06-01,12:00:00,101,102,100,101.5,800,bar\n"
    )
    bars = load_bars_csv(csv, spec)
    assert len(bars) == 2
    assert bars[0].session == "open"
    assert bars[0].high == 101.0 and bars[0].volume == 1000.0
    assert bars[1].session == "midday"


def test_load_bars_csv_epoch_seconds(tmp_path):
    spec = make_spec()
    csv = tmp_path / "epoch.csv"
    # 2026-06-01T13:35:00Z == 1780666500 epoch seconds -> 09:35 ET
    csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        "1780666500,100,101,99,100.5,1000\n"
    )
    bars = load_bars_csv(csv, spec)
    assert len(bars) == 1
    assert bars[0].ts.astimezone(ET).hour == 9 and bars[0].ts.astimezone(ET).minute == 35
    assert bars[0].session == "open"


def test_normalize_csv_resamples_to_10m(tmp_path):
    spec = make_spec()
    src = tmp_path / "one_min.csv"
    # five 1-minute bars from 09:35; resample to 5 -> one bar (first/max/min/last/sum)
    lines = ["timestamp,open,high,low,close,volume"]
    vals = [(100, 101, 99, 100.5), (100.5, 102, 100, 101), (101, 103, 100.5, 102),
            (102, 102.5, 101, 101.5), (101.5, 104, 101, 103)]
    for i, (o, h, l, c) in enumerate(vals):
        lines.append(f"2026-06-01T13:35:0{i}+00:00,{o},{h},{l},{c},10")
    src.write_text("\n".join(lines) + "\n")
    out = tmp_path / "out.csv"
    from msf_trader.backtest.data import normalize_csv
    normalize_csv(src, out, spec, resample_minutes=5, rth_only=True)
    bars = load_bars_csv(out, spec)
    assert len(bars) == 1
    b = bars[0]
    assert (b.open, b.high, b.low, b.close, b.volume) == (100.0, 104.0, 99.0, 103.0, 50.0)


def test_prior_day_levels():
    spec = make_spec()
    day1 = build(spec, [(100, 110, 90, 105), (105, 112, 100, 108)], date="2026-06-01")
    day2 = build(spec, [(108, 109, 107, 108)], date="2026-06-02")
    levels = prior_day_levels(day1 + day2)
    d2 = day2[0].date
    assert levels[d2]["high"] == 112.0
    assert levels[d2]["low"] == 90.0
