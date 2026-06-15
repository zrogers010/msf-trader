"""Execution layer for the swing strategy.

Two implementations behind one tiny interface:

- `PaperBroker`  — fully local, JSON-persisted simulated account. Fills at the
  reference price. Use this to run the whole daily loop with zero risk and no
  broker connection.

- `RobinhoodMCPBroker` — maps each action to a Robinhood Agentic Trading MCP
  tool (get_portfolio / get_equity_quotes / review_equity_order /
  place_equity_order). Robinhood exposes these as an MCP server
  (https://agent.robinhood.com/mcp/trading) that an AI agent calls; this adapter
  takes a `call_tool(name, **args)` callable so the agent (or a test mock) can
  wire the actual invocation. It never stores credentials itself.

Nothing here bypasses Robinhood's own pre-trade review or approval settings.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Protocol

from .signals import PlannedOrder, Position


class Broker(Protocol):
    def get_equity(self) -> float: ...
    def get_cash(self) -> float: ...
    def get_positions(self) -> list[Position]: ...
    def execute(self, order: PlannedOrder) -> dict: ...


class PaperBroker:
    """Simulated broker with JSON-persisted state. Safe for dry runs."""

    def __init__(self, state_path: str | Path = "data/paper_account.json", starting_cash: float = 100_000.0):
        self.state_path = Path(state_path)
        if self.state_path.exists():
            self._state = json.loads(self.state_path.read_text())
        else:
            self._state = {"cash": starting_cash, "positions": {}, "history": []}

    # --- account ---------------------------------------------------------
    def get_cash(self) -> float:
        return float(self._state["cash"])

    def get_positions(self) -> list[Position]:
        return [Position(**p) for p in self._state["positions"].values()]

    def get_equity(self, marks: dict[str, float] | None = None) -> float:
        marks = marks or {}
        pos_val = sum(
            p["quantity"] * marks.get(s, p["entry_price"])
            for s, p in self._state["positions"].items()
        )
        return self.get_cash() + pos_val

    # --- lifecycle -------------------------------------------------------
    def age_positions(self) -> None:
        """Increment bars_held by one trading day. Call once per trading day."""
        for p in self._state["positions"].values():
            p["bars_held"] = int(p.get("bars_held", 0)) + 1

    def execute(self, order: PlannedOrder, asof: date | None = None) -> dict:
        asof = asof or date.today()
        positions = self._state["positions"]
        if order.action == "BUY":
            qty = (order.notional or 0.0) / order.price
            positions[order.symbol] = asdict(Position(
                symbol=order.symbol, quantity=qty, entry_price=order.price,
                entry_date=asof.isoformat(), bars_held=0))
            self._state["cash"] -= order.notional or 0.0
            fill = {"symbol": order.symbol, "side": "buy", "qty": qty, "price": order.price}
        else:  # SELL
            pos = positions.pop(order.symbol, None)
            qty = order.quantity or (pos["quantity"] if pos else 0.0)
            self._state["cash"] += qty * order.price
            fill = {"symbol": order.symbol, "side": "sell", "qty": qty, "price": order.price}
        self._state["history"].append({"date": asof.isoformat(), "reason": order.reason, **fill})
        return fill

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2))


class RobinhoodMCPBroker:
    """Adapter onto the Robinhood Agentic Trading MCP tools.

    `call_tool` is a callable `(tool_name, **kwargs) -> dict` that performs the
    actual MCP invocation. When running inside an AI agent that has the Robinhood
    Trading MCP connected, the agent supplies this. For tests, pass a mock.
    """

    def __init__(self, call_tool: Callable[..., dict], review_before_place: bool = True):
        self.call = call_tool
        self.review_before_place = review_before_place

    def get_portfolio(self) -> dict:
        return self.call("get_portfolio")

    def get_cash(self) -> float:
        return float(self.get_portfolio().get("buying_power", 0.0))

    def get_equity(self) -> float:
        return float(self.get_portfolio().get("total_value", 0.0))

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        for p in self.call("get_equity_positions").get("positions", []):
            out.append(Position(
                symbol=p["symbol"], quantity=float(p["quantity"]),
                entry_price=float(p.get("average_buy_price", 0.0)),
                entry_date=p.get("created_at", "")[:10], bars_held=0))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, float]:
        res = self.call("get_equity_quotes", symbols=symbols)
        return {q["symbol"]: float(q["last_trade_price"]) for q in res.get("quotes", [])}

    def execute(self, order: PlannedOrder) -> dict:
        args = dict(symbol=order.symbol, side=order.action.lower(), type="market")
        if order.action == "BUY":
            args["amount_in_dollars"] = order.notional  # fractional notional order
        else:
            args["quantity"] = order.quantity
        if self.review_before_place:
            review = self.call("review_equity_order", **args)
            if review.get("blocked"):
                return {"placed": False, "review": review}
        placed = self.call("place_equity_order", **args)
        return {"placed": True, "order": placed}


class AlpacaPaperBroker:
    """Real broker PAPER account via Alpaca's trading API.

    Unlike PaperBroker (local sim), this routes orders to Alpaca's paper
    environment (https://paper-api.alpaca.markets): realistic fills, positions,
    equity, and a dashboard. Free; uses PAPER trading keys (separate from live).

    Credentials (in .env), tried in order:
      ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY   (preferred, paper-specific)
      APCA_API_KEY_ID     / APCA_API_SECRET_KEY        (if those are paper keys)

    A small sidecar JSON tracks entry_date / bars_held per symbol (Alpaca does not
    expose a swing-day counter), so the time-stop and aging work."""

    BASE = "https://paper-api.alpaca.markets"

    def __init__(self, state_path: str | Path = "data/alpaca_paper_state.json",
                 key_id: str | None = None, secret: str | None = None):
        from dotenv import load_dotenv
        load_dotenv()
        self.key = key_id or os.environ.get("ALPACA_PAPER_KEY_ID") or os.environ.get("APCA_API_KEY_ID")
        self.secret = secret or os.environ.get("ALPACA_PAPER_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
        if not self.key or not self.secret:
            raise RuntimeError(
                "Alpaca paper keys not set. Generate PAPER trading keys at "
                "https://app.alpaca.markets/ (Paper Trading -> API keys) and add "
                "ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY to .env."
            )
        self.state_path = Path(state_path)
        self._holds = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    # --- HTTP ------------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.BASE + path, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                txt = resp.read().decode()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Alpaca {method} {path} -> {e.code}: {e.read().decode()[:300]}") from e

    # --- account ---------------------------------------------------------
    def _account(self) -> dict:
        return self._req("GET", "/v2/account")

    def is_market_open(self) -> bool:
        """True if the US equity market is currently open (Alpaca clock)."""
        return bool(self._req("GET", "/v2/clock").get("is_open"))

    def get_cash(self) -> float:
        return float(self._account()["cash"])

    def get_equity(self) -> float:
        return float(self._account()["equity"])

    def get_positions(self) -> list[Position]:
        raw = self._req("GET", "/v2/positions")
        out: list[Position] = []
        live_syms = set()
        for p in raw:
            sym = p["symbol"]; live_syms.add(sym)
            h = self._holds.get(sym, {})
            out.append(Position(
                symbol=sym, quantity=float(p["qty"]),
                entry_price=float(p["avg_entry_price"]),
                entry_date=h.get("entry_date", date.today().isoformat()),
                bars_held=int(h.get("bars_held", 0))))
        # drop sidecar entries for positions Alpaca no longer holds
        self._holds = {s: v for s, v in self._holds.items() if s in live_syms}
        return out

    def age_positions(self) -> None:
        for v in self._holds.values():
            v["bars_held"] = int(v.get("bars_held", 0)) + 1

    # --- execution -------------------------------------------------------
    def execute(self, order: PlannedOrder, asof: date | None = None) -> dict:
        asof = asof or date.today()
        if order.action == "BUY":
            body = {"symbol": order.symbol, "side": "buy", "type": "market",
                    "time_in_force": "day", "notional": round(order.notional or 0.0, 2)}
            res = self._req("POST", "/v2/orders", body)
            self._holds[order.symbol] = {"entry_date": asof.isoformat(), "bars_held": 0}
            return {"placed": True, "order_id": res.get("id"), "status": res.get("status")}
        else:  # SELL -> close the whole position
            res = self._req("DELETE", f"/v2/positions/{order.symbol}")
            self._holds.pop(order.symbol, None)
            return {"placed": True, "order_id": res.get("id"), "status": res.get("status")}

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._holds, indent=2))
