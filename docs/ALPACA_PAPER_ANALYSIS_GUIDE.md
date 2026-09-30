# Alpaca Paper Trading: Performance Review & Improvement Guide

**Status:** Analysis prepared for post-401-fix review  
**Context:** MSF Trader paper trading was blocked by Alpaca 401 on `/v2/clock`. Once credentials are fixed, this guide maps how to pull trade history, analyze performance, and identify improvement levers.

---

## 1. Trade History & PnL Data Sources

### 1.1 In-Repo State Files

**`data/alpaca_paper_state.json`** (local sidecar)
- **Purpose:** Tracks entry dates and bars_held per position (Alpaca API doesn't provide swing-day counters)
- **Contains:** `{"SYMBOL": {"entry_date": "YYYY-MM-DD", "bars_held": N}}`
- **Does NOT contain:** Full fill history, PnL, exit prices
- **Use:** Time-stop logic only; not suitable for performance analysis

**`data/paper_account.json`** (local PaperBroker)
- **Only used if running `--broker paper` (local simulation)**
- **Contains:** Full history array with entries/exits, prices, reasons
- **NOT used for Alpaca paper trading** (that goes to Alpaca's servers)

**`logs/swing-YYYYMMDD.log`** (scheduled run output)
- **Location:** `/Users/zach/code/src/msf-trader/logs/`
- **Contains:** Daily order plans printed by CLI (what was placed, not fills)
- **Format:** Human-readable table, not machine-parseable
- **Limitation:** Shows intent, not actual fills or slippage

### 1.2 Alpaca Paper Account API (Source of Truth)

**Live trade/fill history lives on Alpaca's servers.** Query via their API:

#### Account Summary
```bash
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/account
```
Returns: `equity`, `cash`, `buying_power`, `portfolio_value`, `last_equity` (prev close)

#### Current Positions
```bash
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/positions
```
Returns: Open positions with `symbol`, `qty`, `avg_entry_price`, `market_value`, `unrealized_pl`

#### Order History (Closed Trades)
```bash
# All orders (max 500 per call, paginate with ?after=)
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     "https://paper-api.alpaca.markets/v2/orders?status=closed&limit=500"
```
Returns: Array of orders with `symbol`, `side`, `filled_avg_price`, `filled_at`, `filled_qty`, `order_type`

#### Portfolio History (Equity Curve)
```bash
# Daily equity snapshots (P&L over time)
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     "https://paper-api.alpaca.markets/v2/account/portfolio/history?period=1M&timeframe=1D"
```
Returns: Time series of `equity`, `profit_loss`, `profit_loss_pct`

**Recommendation:** Write a CLI command to fetch and cache this data locally for analysis.

---

## 2. Existing Performance/Reporting Tools

### 2.1 Current State (None for Live Paper)

**❌ No built-in reporting CLI** for Alpaca paper trading  
**❌ No performance metrics calculation** for live fills  
**✅ Only backtest reporting** exists (`msf-trader swing-backtest`)

The backtest CLI (`swing-backtest`) computes:
- CAGR, Sharpe, max drawdown, exposure
- Per-year returns, winning years
- Trade counts per instrument

**But this analyzes historical daily bars, NOT live Alpaca fills.**

### 2.2 What's Missing (Build This)

A new CLI command to:
1. Fetch closed orders from Alpaca API
2. Match BUY/SELL pairs into round-trip trades
3. Compute realized P&L per trade (accounting for fills, not model prices)
4. Calculate portfolio metrics (win rate, Sharpe, max DD from equity curve)
5. Compare live vs backtest (are fills worse than model? slippage analysis)

**Suggested CLI:**
```bash
msf-trader swing-report --broker alpaca --since 2026-09-01
```

Output:
- Total P&L, win rate, avg hold time
- Per-symbol breakdown (which names worked/failed)
- Fills vs model prices (slippage analysis)
- Equity curve plot (if matplotlib available)

---

## 3. Concrete Improvement Levers (Ranked, Strategy-Grounded)

*Note: Label these as **hypotheses** until we have live fill data. Validate with backtests + live paper comparison.*

### Tier 1: High-Impact, Low-Risk (Test First)

#### 1. **Entry RSI Threshold Tuning** (`rsi_buy`)
- **Current:** `10.0` (deeply oversold)
- **Lever:** `Rsi2Params.rsi_buy` (line 46, `src/msf_trader/swing/rsi2.py`)
- **Hypothesis:** RSI(2) < 10 may be too selective (misses recoveries); RSI < 15 could capture more mean-reversion while staying oversold
- **Backtest range:** 5, 10, 15, 20
- **Risk:** Lower threshold = more trades = higher costs; validate cost drag
- **Code:**
  ```python
  params = Rsi2Params(rsi_buy=15.0)  # test 15 vs 10
  ```

#### 2. **Exit SMA Period** (`exit_sma`)
- **Current:** `5`-day SMA (quick exit on bounce)
- **Lever:** `Rsi2Params.exit_sma` (line 49)
- **Hypothesis:** 5-day may be too fast (exits early, leaving profit on table); 7-10 day could let winners run
- **Backtest range:** 3, 5, 7, 10
- **Risk:** Longer hold = more drawdown exposure during reversals
- **Code:**
  ```python
  params = Rsi2Params(exit_sma=7)
  ```

#### 3. **Max Hold Time Stop** (`max_hold`)
- **Current:** `10` trading days
- **Lever:** `Rsi2Params.max_hold` (line 50)
- **Hypothesis:** 10 days may let losers linger; 7-day stop could cut tail losses faster
- **Backtest range:** 5, 7, 10, 15
- **Risk:** Too short = cuts off slow recoveries
- **Code:**
  ```python
  params = Rsi2Params(max_hold=7)
  ```

### Tier 2: Regime Filters (Moderate Impact)

#### 4. **Regime SMA Period** (`regime_sma`)
- **Current:** `200`-day (long-term trend filter)
- **Lever:** `Rsi2Params.regime_sma` (line 48)
- **Hypothesis:** 200-day is slow; 50-day or 100-day could be more responsive (but test whipsaw risk)
- **Backtest range:** 50, 100, 150, 200
- **Risk:** Shorter SMA = more false positives in choppy markets
- **Code:**
  ```python
  params = Rsi2Params(regime_sma=100)
  ```

#### 5. **No-Regime Mode (Capture All Dips)**
- **Current:** Only buys dips above 200-day SMA
- **Lever:** `regime=False` in `portfolio_backtest()`
- **Hypothesis:** Missing profitable dips below SMA in strong recoveries
- **Test:** Compare Sharpe/DD with `regime=True` vs `False`
- **Risk:** Catching falling knives in sustained downtrends
- **Code:**
  ```python
  portfolio_backtest(regime=False)
  ```

### Tier 3: Sizing & Risk (Higher Risk, Test Last)

#### 6. **Position Sizing** (`max_weight`, `fixed_notional`)
- **Current:** `max_weight=0.167` (~6% per name, or $1000 fixed in scheduled run)
- **Lever:** `Rsi2Params.max_weight` (line 52) or CLI `--dollars` flag
- **Hypothesis:** Fixed dollar sizing doesn't compound gains; %-of-equity sizing could improve CAGR (but increases volatility)
- **Test:** Compare $1000/trade vs 10% equity/trade over 6 months
- **Risk:** Higher % = larger drawdowns on losers
- **Code:**
  ```python
  params = Rsi2Params(max_weight=0.10)  # 10% per name
  run_daily(..., fixed_notional=None)  # let it compound
  ```

#### 7. **Target Position Slots** (`target_positions`)
- **Current:** `6` concurrent positions (default in backtest)  
  `10` in production (`scripts/run_swing.sh --slots 10`)
- **Lever:** CLI `--slots` flag
- **Hypothesis:** 10 slots spreads risk more (lower single-name tail) but reduces per-name exposure
- **Test:** 5, 6, 8, 10 slots; measure Sharpe vs max DD
- **Risk:** More slots = more trades = higher costs
- **Code:**
  ```bash
  msf-trader swing-plan --broker alpaca --slots 8
  ```

#### 8. **Universe Expansion/Contraction**
- **Current:** 20 ETFs (`DEFAULT_UNIVERSE`, line 38-40 in `rsi2.py`)
- **Lever:** `--symbols` flag or hardcode different list
- **Hypothesis:** Some ETFs (low liquidity, high spread) may drag performance; focus on SPY/QQQ/SMH top performers
- **Test:** Run backtest on top-5 vs full-20 universe
- **Risk:** Concentration risk if narrowing; test survivorship bias
- **Code:**
  ```python
  universe = ["SPY", "QQQ", "SMH", "XLK", "IWM"]  # top 5
  ```

### Tier 4: Structural (Needs Code Changes)

#### 9. **Multi-Timeframe Confirmation**
- **Idea:** Only enter if both daily RSI(2) < 10 AND hourly RSI(14) is oversold
- **Benefit:** Reduce whipsaws, higher-quality entries
- **Cost:** Requires intraday data; not currently supported

#### 10. **Profit Target Exit (in addition to SMA)**
- **Idea:** Exit 50% of position at +2% gain, let 50% ride to SMA or time stop
- **Benefit:** Lock in quick wins, let runners compound
- **Cost:** Adds complexity; may reduce total return if exits are premature

---

## 4. Next Steps (Actionable Workflow)

### Immediate (Post-401-Fix)

1. **Fix Alpaca credentials** (see main PR)
2. **Pull historical fills:**
   ```bash
   # Manual API call or write a CLI wrapper
   curl ... > data/alpaca_order_history.json
   ```
3. **Compare live vs backtest:**
   - Actual fills vs model expectations
   - Slippage analysis (were market orders filled worse than close prices?)
   - Win rate & Sharpe: does live match backtest projections?

### Short-Term (Next 1-2 Weeks)

4. **Build `swing-report` CLI** to automate step 2
5. **Run parameter sweeps** on historical data:
   ```bash
   # Sweep RSI buy threshold
   for rsi in 5 10 15 20; do
     msf-trader swing-backtest --rsi-buy $rsi
   done
   ```
6. **Test top 3 levers** in live paper (one at a time):
   - RSI threshold 15 vs 10
   - Exit SMA 7 vs 5
   - Max hold 7 vs 10

### Medium-Term (Next 1-2 Months)

7. **Forward-test best params** for 20+ trades
8. **Analyze symbol-level performance** (which ETFs contribute most/least?)
9. **Consider regime adjustments** (e.g. disable strategy during VIX > 30?)

---

## 5. Key Files Reference

| What | Path | Purpose |
|------|------|---------|
| Strategy params | `src/msf_trader/swing/rsi2.py:45-53` | `Rsi2Params` dataclass |
| Entry/exit logic | `src/msf_trader/swing/signals.py:56-119` | `compute_daily_plan()` |
| Backtest metrics | `src/msf_trader/swing/rsi2.py:152-201` | `portfolio_backtest()` |
| Alpaca broker | `src/msf_trader/swing/broker.py:141-253` | `AlpacaPaperBroker` |
| CLI entry point | `src/msf_trader/cli.py:462-520` | `swing-plan` command |
| Scheduled run | `scripts/run_swing.sh` | Production launchd wrapper |
| Local state | `data/alpaca_paper_state.json` | Entry dates (not fills) |

---

## 6. Hypotheses to Validate with Live Data

**Do NOT change production settings until these are tested:**

| Hypothesis | Test Method | Success Metric |
|------------|-------------|----------------|
| RSI 15 > RSI 10 | Side-by-side backtest 2020-2026 | Higher Sharpe, similar max DD |
| Exit SMA 7 > 5 | Backtest + 20 live trades | Avg profit per trade +10% |
| 7-day stop < 10-day | Backtest + check tail losses | Fewer -5%+ losers |
| Regime=False captures more | Backtest full-cycle bear (2022) | Net positive in down year |
| Fixed $1k hurts compounding | 6-month forward test | Equity curve diverges from % sizing |

**Remember:** Paper trading is free experimentation. Test aggressively, but only graduate params to "live" after they beat the baseline in BOTH backtest AND 30+ paper trades.

---

## 7. Warning: Overfitting Risk

**All backtests use the SAME historical data.** Improvements that look great in-sample may not persist. Mitigations:

1. **Out-of-sample test:** Optimize on 2004-2019, validate on 2020-2026
2. **Cross-instrument validation:** If a param change helps SPY but hurts QQQ, it's fragile
3. **Live forward-test:** 50+ paper trades before trusting a change
4. **Regime diversity:** Must work in both bull (2020-2021) and bear (2022) periods

Do not chase small Sharpe improvements (0.7 → 0.75) by tuning 5 knobs. The edge is in the mean-reversion thesis, not the parameters.

---

**End of Guide. Ready to pull Alpaca data and rank improvements once 401 is resolved.**
