"""Tests for the RSI(2) swing strategy: indicators, daily plan, paper broker."""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from msf_trader.swing import (
    Rsi2Params,
    wilder_rsi,
    compute_daily_plan,
    Position,
    PaperBroker,
    RobinhoodMCPBroker,
)


def _series_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"date": dates, "open": c, "high": c, "low": c, "close": c, "volume": 1.0})


def test_wilder_rsi_bounds_and_oversold():
    falling = list(np.linspace(100, 80, 30))
    rsi = wilder_rsi(pd.Series(falling), 2)
    assert 0 <= rsi.iloc[-1] <= 100
    assert rsi.iloc[-1] < 10  # a straight decline is deeply oversold on RSI(2)


def test_entry_fires_on_oversold_dip_in_uptrend():
    # long uptrend (above 200-SMA) then a sharp multi-day dip -> RSI2 collapses
    up = list(np.linspace(100, 300, 240))
    dip = [295, 285, 272, 260]
    bars = {"AAA": _series_bars(up + dip)}
    plan = compute_daily_plan(date(2021, 1, 1), bars, positions=[],
                              equity=100_000, cash=100_000, params=Rsi2Params())
    buys = [o for o in plan if o.action == "BUY"]
    assert len(buys) == 1 and buys[0].symbol == "AAA"
    assert buys[0].notional == pytest.approx(20_000)  # 20% max-weight slot


def test_no_entry_below_regime_filter():
    # same dip but in a DOWNtrend (below 200-SMA) -> regime filter blocks it
    down = list(np.linspace(300, 100, 240))
    dip = [98, 95, 92, 90]
    bars = {"AAA": _series_bars(down + dip)}
    plan = compute_daily_plan(date(2021, 1, 1), bars, positions=[],
                              equity=100_000, cash=100_000, params=Rsi2Params())
    assert [o for o in plan if o.action == "BUY"] == []


def test_exit_on_bounce_above_exit_sma():
    # hold a position; once price closes back above the 5-SMA we exit
    closes = list(np.linspace(100, 300, 240)) + [290, 280, 270, 260, 330]
    bars = {"AAA": _series_bars(closes)}
    pos = [Position("AAA", quantity=10, entry_price=270, entry_date="2021-01-01", bars_held=2)]
    plan = compute_daily_plan(date(2021, 1, 5), bars, pos,
                              equity=100_000, cash=50_000, params=Rsi2Params())
    sells = [o for o in plan if o.action == "SELL"]
    assert len(sells) == 1 and sells[0].symbol == "AAA" and sells[0].quantity == 10


def test_time_stop_exit():
    # price still below exit SMA, but max_hold reached -> time-stop exit
    closes = list(np.linspace(300, 100, 244))
    bars = {"AAA": _series_bars(closes)}
    pos = [Position("AAA", quantity=5, entry_price=120, entry_date="2021-01-01", bars_held=10)]
    plan = compute_daily_plan(date(2021, 1, 5), bars, pos,
                              equity=50_000, cash=20_000, params=Rsi2Params(max_hold=10))
    assert any(o.action == "SELL" and "time stop" in o.reason for o in plan)


def test_paper_broker_roundtrip(tmp_path: Path):
    from msf_trader.swing.signals import PlannedOrder

    state = tmp_path / "acct.json"
    b = PaperBroker(state, starting_cash=10_000)
    b.execute(PlannedOrder("AAA", "BUY", "entry", price=100.0, notional=2_000), date(2021, 1, 1))
    assert b.get_cash() == pytest.approx(8_000)
    assert b.get_positions()[0].quantity == pytest.approx(20)
    b.age_positions()
    assert b.get_positions()[0].bars_held == 1
    b.execute(PlannedOrder("AAA", "SELL", "exit", price=110.0, quantity=20), date(2021, 1, 6))
    assert b.get_cash() == pytest.approx(10_200)  # +$200 profit
    assert b.get_positions() == []
    b.save()
    assert state.exists()


def test_robinhood_mcp_adapter_calls_review_then_place():
    from msf_trader.swing.signals import PlannedOrder

    calls = []

    def mock_call(name, **kw):
        calls.append((name, kw))
        if name == "review_equity_order":
            return {"blocked": False}
        return {"id": "ord_123", "state": "queued"}

    adapter = RobinhoodMCPBroker(mock_call)
    res = adapter.execute(PlannedOrder("SMH", "BUY", "entry", price=250.0, notional=20_000))
    assert res["placed"] is True
    assert calls[0][0] == "review_equity_order"
    assert calls[1][0] == "place_equity_order"
    assert calls[1][1]["amount_in_dollars"] == 20_000
    assert calls[1][1]["side"] == "buy"


def test_alpaca_paper_broker_routes_buy_and_sell(tmp_path: Path):
    from msf_trader.swing.broker import AlpacaPaperBroker
    from msf_trader.swing.signals import PlannedOrder

    b = AlpacaPaperBroker(state_path=tmp_path / "alp.json", key_id="k", secret="s")
    reqs = []

    def fake_req(method, path, body=None):
        reqs.append((method, path, body))
        if path == "/v2/positions" and method == "GET":
            return [{"symbol": "SPY", "qty": "10", "avg_entry_price": "400.0"}]
        return {"id": "ord_1", "status": "accepted"}

    b._req = fake_req  # type: ignore[assignment]

    buy = b.execute(PlannedOrder("SMH", "BUY", "entry", price=250.0, notional=20_000),
                    date(2021, 1, 4))
    assert buy["placed"] is True
    assert reqs[-1] == ("POST", "/v2/orders",
                        {"symbol": "SMH", "side": "buy", "type": "market",
                         "time_in_force": "day", "notional": 20000.0})
    assert b._holds["SMH"]["entry_date"] == "2021-01-04"

    sell = b.execute(PlannedOrder("SMH", "SELL", "exit", price=260.0, quantity=80))
    assert sell["placed"] is True
    assert reqs[-1] == ("DELETE", "/v2/positions/SMH", None)
    assert "SMH" not in b._holds

    # positions merge Alpaca truth with the sidecar bars_held counter
    b._holds["SPY"] = {"entry_date": "2021-01-01", "bars_held": 3}
    pos = b.get_positions()
    assert pos[0].symbol == "SPY" and pos[0].bars_held == 3
