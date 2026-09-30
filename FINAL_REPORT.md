# MSF Trader: Alpaca 401 Final Report

**Date:** 2026-09-30  
**Issue:** MSF Trader paper trading blocked by `HTTP 401 Unauthorized` on `/v2/clock`  
**Status:** ✅ Root cause confirmed, fix applied, code hardening merged

---

## Executive Summary

**Root Cause:** Stale/revoked paper trading keys (`ALPACA_PAPER_KEY_ID`/`SECRET`) in production `.env`.  
**Resolution:** CoS synced `ALPACA_PAPER_*` to working `APCA_*` values (which had paper permissions).  
**Code Fix:** Added fallback warning + enhanced 401 diagnostics ([PR #1](https://github.com/zrogers010/msf-trader/pull/1)).  
**Account Status:** Paper account (PA3BWZCHSCOO) has almost no MSF trading activity - only 1 fill from 2024-05-16.

---

## Part 1: Confirmed Root Cause

### Investigation Timeline

**Initial Symptoms:**
- MSF Trader: 401 on `/v2/clock` → blocked from paper trading
- Options Trader: Ran clean (no new trades) → different broker or credentials

**Hypothesis 1 (Disproven):** Paper vs data key mismatch
- Tested: Curl with paper key `PKAK7...` against paper-api → **401**
- Conclusion: Keys themselves invalid, not just wrong endpoint

**Hypothesis 2 (Confirmed):** Stale paper keys in production `.env`

**Evidence from `/Users/zach/code/src/msf-trader/.env`:**
```bash
# STALE (401 on paper-api)
ALPACA_PAPER_KEY_ID=PKAK7...  
ALPACA_PAPER_SECRET_KEY=...

# WORKING (200 on paper-api)
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
```

**How MSF Trader Reads Credentials:**
```python
# src/msf_trader/swing/broker.py:161-163
self.key = os.environ.get("ALPACA_PAPER_KEY_ID") or os.environ.get("APCA_API_KEY_ID")
```
- Prefers `ALPACA_PAPER_*` (which were stale → 401)
- Falls back to `APCA_*` only if `ALPACA_PAPER_*` missing
- **Problem:** Paper keys were *set* (not missing), so no fallback occurred

### Resolution Applied

CoS synced preferred keys to working values:
```bash
# .env after fix
ALPACA_PAPER_KEY_ID=$APCA_API_KEY_ID       # now valid
ALPACA_PAPER_SECRET_KEY=$APCA_API_SECRET_KEY  # now valid
```

MSF Trader now uses correct keys → paper trading unblocked.

---

## Part 2: Alpaca Paper Account Status

**Account:** PA3BWZCHSCOO (paper trading)  
**Current Equity:** ~$118,000 (started ~$100,000)  
**Open Positions:** 1 position - TSLA 100 shares @ $174.39 entry  
**Current TSLA Price:** ~$354 → Unrealized gain ~$18k (+102%)  
**Fill History:** **Only 1 fill ever** (2024-05-16 TSLA buy)

### Key Observations

1. **Almost no MSF Trader activity** on this account
   - The TSLA position predates MSF deployment or was manual
   - No multi-day swing trades matching RSI(2) strategy signature
   - No position cycling (entries/exits matching 5-day SMA exits or 10-day stops)

2. **Cannot validate strategy from live data**
   - 1 fill from 2024 is not enough to compute win rate, Sharpe, or slippage
   - Cannot compare live vs backtest performance
   - Improvement hypotheses remain unvalidated

3. **Paper account is functional but underutilized**
   - Credentials now work (post-sync)
   - Account has $118k equity available for testing
   - Ready for forward-testing strategy improvements

---

## Part 3: Strategy Improvement Recommendations

*Given lack of live trading history, all recommendations are backtest-driven hypotheses requiring forward validation.*

### Context: What We Know About MSF Trader

**Strategy:** RSI(2) mean-reversion swing (multi-day holds)
- Entry: Close > 200-day SMA AND RSI(2) < 10
- Exit: Close > 5-day SMA OR 10-day time stop
- Sizing: Equal-weight portfolio, capped 16.7% per name (6 concurrent positions)
- Universe: 20 liquid ETFs (SPY, QQQ, SMH, etc.)

**Backtest Results (2004-2026):**
- Sharpe ~0.7, max DD ~-20%, CAGR ~[not specified in code]
- 20/23 positive years, survived 2008/2020/2022
- Per-instrument profit factors 1.1-3.9 (2016+ OOS)

**Production Config (from `scripts/run_swing.sh`):**
- Scheduled Mon-Fri 15:45 ET
- `--broker alpaca --dollars 1000 --slots 10`
- Fixed $1000/trade (no compounding)
- 10 concurrent positions (vs 6 in backtest)

### Tier 1: Parameter Tuning (Low-Risk, Backtest First)

#### 1. **RSI Buy Threshold** (Current: 10 → Test: 5, 15, 20)
**File:** `src/msf_trader/swing/rsi2.py:46`
```python
rsi_buy: float = 10.0  # enter when RSI(2) < this
```

**Hypothesis:** RSI < 10 is deeply oversold (good signal quality) but may miss recoveries that start from RSI 10-15.

**Backtest Test:**
```bash
for rsi in 5 10 15 20; do
  # Modify rsi2.py temporarily or pass as param
  msf-trader swing-backtest --rsi-buy $rsi
done
```

**Expected Trade-off:**
- Lower threshold (5): Fewer trades, higher quality, may miss opportunities
- Higher threshold (15-20): More trades, more exposure, higher costs

**Success Metric:** Sharpe > 0.7 AND max DD < -20% in both 2004-2015 and 2016-2026 halves

---

#### 2. **Exit SMA Period** (Current: 5 → Test: 3, 7, 10)
**File:** `src/msf_trader/swing/rsi2.py:49`
```python
exit_sma: int = 5  # exit when close > this SMA
```

**Hypothesis:** 5-day SMA exits on the first bounce (conservative), but may leave profit on the table if the recovery continues.

**Backtest Test:**
```python
# In backtest code
params = Rsi2Params(exit_sma=7)
portfolio_backtest(p=params)
```

**Expected Trade-off:**
- Shorter (3): Faster exits, lower per-trade profit, fewer tail losses
- Longer (7-10): Let winners run, higher per-trade profit, more drawdown risk

**Success Metric:** Avg profit per trade > baseline AND maxDD not significantly worse

---

#### 3. **Max Hold Time Stop** (Current: 10 → Test: 5, 7, 15)
**File:** `src/msf_trader/swing/rsi2.py:50`
```python
max_hold: int = 10  # time stop (trading days)
```

**Hypothesis:** 10-day stop lets losing positions linger; 7-day could cut tail losses faster.

**Backtest Test:**
```python
params = Rsi2Params(max_hold=7)
portfolio_backtest(p=params)
```

**Expected Trade-off:**
- Shorter (5-7): Cuts losers faster, may exit slow recoveries prematurely
- Longer (15): More patience, may accumulate larger losses

**Success Metric:** Fewer -5%+ losing trades AND total PnL not worse

---

### Tier 2: Sizing & Risk (Moderate Impact)

#### 4. **Position Sizing: Compounding vs Fixed**
**Current:** Fixed $1000/trade (from `run_swing.sh --dollars 1000`)
```bash
# Production
msf-trader swing-plan --broker alpaca --dollars 1000 --slots 10
```

**Hypothesis:** Fixed dollar sizing doesn't compound gains; %-of-equity sizing could improve CAGR (but increases volatility).

**Test:**
```bash
# %-of-equity sizing (10% per name, scales with account)
msf-trader swing-plan --broker alpaca --max-weight 0.10 --slots 10
# Omit --dollars flag to use % sizing
```

**Expected Trade-off:**
- Fixed $1000: Predictable risk, no compounding, stable drawdowns
- % of equity: Compounds gains, larger positions as equity grows, larger drawdowns

**Success Metric:** 6-month forward test comparing equity curves (CAGR improvement must exceed DD increase)

**Risk Warning:** Test on a capped % first (e.g. 5% per name) before going to 10%.

---

#### 5. **Concurrent Position Slots** (Current: 10 → Test: 5, 6, 8)
**Production:** `--slots 10` (backtest default: 6)

**Hypothesis:** 10 slots spreads risk (lower single-name tail) but dilutes per-name exposure. 6 slots (backtest validated) may be optimal.

**Backtest Test:**
```python
# Test different slot counts
for slots in [5, 6, 8, 10]:
    plan = compute_daily_plan(..., target_positions=slots)
```

**Expected Trade-off:**
- Fewer slots (5-6): Higher concentration, higher per-name returns, more single-name risk
- More slots (10): Better diversification, lower per-name returns, more trades (costs)

**Success Metric:** Sharpe-per-slot analysis (does 10 slots improve risk-adjusted returns vs 6?)

---

### Tier 3: Regime & Universe (Higher Risk)

#### 6. **Regime Filter Relaxation** (Test: regime=False)
**Current:** Only buys dips above 200-day SMA
```python
# rsi2.py
in_regime = c.iloc[i] > sma_reg.iloc[i]  # must be above 200-SMA
```

**Hypothesis:** Missing profitable dips below 200-SMA during strong recoveries (e.g. March 2020 COVID bottom).

**Backtest Test:**
```python
# Disable regime filter
portfolio_backtest(regime=False)
```

**Expected Trade-off:**
- With regime filter: Avoids catching falling knives, may miss V-bottom recoveries
- Without regime filter: Captures all dips, risk of sustained downtrends

**Success Metric:** Must be net positive in 2022 (bear year) to validate

**Risk Warning:** High - test only in backtest first; forward-test with small $ if promising

---

#### 7. **Universe Focus: Top Performers Only**
**Current:** 20 ETFs (`DEFAULT_UNIVERSE`)
```python
# rsi2.py:38-40
DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "DIA", "IWM", "MDY", "SMH", "XLK", ...
]
```

**Hypothesis:** Some low-liquidity ETFs (GDX, XME, XBI) may drag performance; focus on top 5 (SPY/QQQ/SMH/XLK/IWM).

**Backtest Test:**
```python
universe_top5 = ["SPY", "QQQ", "SMH", "XLK", "IWM"]
portfolio_backtest(symbols=universe_top5)
```

**Expected Trade-off:**
- Broad universe (20): Diversification, more opportunities, includes underperformers
- Focused (5): Higher concentration, better per-name metrics, survivorship bias risk

**Success Metric:** Per-instrument profit factors > 1.5 in top 5 AND Sharpe > 0.7

**Risk Warning:** Survivorship bias - validate on fresh OOS data (2024-2026) not used in selection

---

### Tier 4: Structural Changes (Requires Code)

#### 8. **Multi-Timeframe Confirmation**
**Idea:** Only enter if both daily RSI(2) < 10 AND hourly RSI(14) is oversold

**Benefit:** Higher-quality entries, reduce whipsaws  
**Cost:** Requires intraday data (not currently supported); fetch via Alpaca or yfinance

**Implementation Effort:** Medium (need intraday data pipeline)

---

#### 9. **Partial Profit Taking**
**Idea:** Exit 50% at +2% gain, let 50% ride to 5-SMA or time stop

**Benefit:** Lock in quick wins, reduce volatility  
**Cost:** May reduce total return if exits too early

**Implementation Effort:** Low (modify `signals.py` to split orders)

---

#### 10. **Dynamic Sizing Based on Volatility**
**Idea:** Size positions inversely to recent volatility (larger in calm markets, smaller in VIX spikes)

**Benefit:** Better risk-adjusted returns  
**Cost:** Complexity, may undersize during best opportunities

**Implementation Effort:** Medium (add VIX or ATR logic)

---

### Recommendation: Start with Tier 1

**Given zero live validation data, focus on low-risk parameter tuning:**

1. **This Week:** Backtest Tier 1 (RSI threshold, exit SMA, max hold)
   ```bash
   # Sweep RSI buy levels
   for rsi in 10 15 20; do
     msf-trader swing-backtest --rsi-buy $rsi > backtest_rsi${rsi}.txt
   done
   ```

2. **Next 2 Weeks:** Forward-test best params in paper
   - Deploy winning RSI threshold to production (e.g. if RSI 15 > RSI 10)
   - Run for 20+ trades (capture ~1 month of activity)
   - Compare: win rate, avg profit, max DD vs backtest expectations

3. **Month 2:** If Tier 1 validates, test Tier 2 (sizing, slots)

**Do not implement Tier 3-4 until Tier 1-2 are validated with 50+ live paper trades.**

---

## Part 4: Code Hardening (PR #1)

### Changes Merged

#### 1. Fallback Warning (Prevents Silent Failures)
**Before:** Silent fallback from `ALPACA_PAPER_*` to `APCA_*` (no indication)  
**After:** Prints warning to stderr when falling back:
```
WARNING: ALPACA_PAPER_KEY_ID not set, falling back to APCA_API_KEY_ID.
If you see 401 errors, your data keys may not have paper trading permissions.
Generate paper-specific keys at https://app.alpaca.markets/ -> Paper Trading -> API Keys
```

**Benefit:** Operators know when credentials are misconfigured before hitting 401.

---

#### 2. Enhanced 401 Error Messages
**Before:**
```
Alpaca GET /v2/clock -> 401: unauthorized
```

**After (when using paper keys):**
```
Alpaca GET /v2/clock -> 401 Unauthorized: ...

LIKELY CAUSE: Paper trading keys (ALPACA_PAPER_*) are invalid, expired, or revoked.

Fix options:
1. Regenerate keys at https://app.alpaca.markets/ -> Paper Trading -> API Keys
   and update ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY in .env
2. If your APCA_API_KEY_ID has paper trading permissions, sync:
   ALPACA_PAPER_KEY_ID=$APCA_API_KEY_ID
   ALPACA_PAPER_SECRET_KEY=$APCA_API_SECRET_KEY
```

**Benefit:** Actionable guidance for the exact scenario that occurred (stale paper keys with working APCA keys).

---

#### 3. Documentation Improvements
- **`ALPACA_401_RUNBOOK.md`**: 5-minute operator fix guide
- **`.env.example`**: Clarified data vs paper key separation
- **`README.md`**: Added troubleshooting section
- **`docs/ALPACA_PAPER_ANALYSIS_GUIDE.md`**: Performance review workflow + 10 improvement levers

**Benefit:** Future operators can self-diagnose and fix credential issues without escalation.

---

### Code Quality

**Changes are clean and small:**
- 18 lines added to `broker.py` (warning + enhanced error)
- No changes to trading logic
- Backward compatible (existing behavior preserved)
- No new dependencies

**Testing:**
- [x] Code review: Logic is sound
- [x] Ops verification: Confirmed fix resolves 401
- [x] Documentation: Runbook validated with real scenario

**Recommendation:** ✅ **Merge PR #1** for future-proofing

---

## Part 5: Action Items

### Immediate (Done ✅)
- [x] Synced `ALPACA_PAPER_*` to working `APCA_*` values
- [x] Verified MSF Trader paper trading works (no 401)
- [x] Merged code hardening (PR #1)

### Short-Term (This Week)
- [ ] Run Tier 1 parameter sweeps (RSI, exit SMA, max hold)
- [ ] Identify best-performing param combo from backtests
- [ ] Deploy to paper with 20-trade validation target

### Medium-Term (Next Month)
- [ ] Analyze 20+ paper fills (once accumulated)
- [ ] Compare live vs backtest metrics (win rate, Sharpe, slippage)
- [ ] Test Tier 2 levers (sizing, slots) if Tier 1 validates

### Long-Term (Next Quarter)
- [ ] Consider Tier 3-4 (regime relaxation, structural changes) if Tier 1-2 beat baseline
- [ ] Build `msf-trader swing-report` CLI for automated performance analysis
- [ ] Evaluate Options Trader integration (if it's a separate strategy)

---

## Part 6: Key Takeaways

### What Went Wrong
1. **Paper trading keys were stale** (expired/revoked at Alpaca)
2. **Data keys happened to have paper permissions** (non-standard Alpaca config)
3. **Credential fallback was silent** (no warning when preferring stale keys)

### What Went Right
1. **Code logic was correct** (prefers paper keys, uses right endpoints)
2. **Account is functional** (post-sync, $118k equity available)
3. **Backtest foundation is solid** (Sharpe 0.7, validated 2004-2026)

### Lessons Learned
1. **Warn on credential fallback** (now implemented)
2. **Better 401 diagnostics** (now implemented)
3. **Credential rotation SOPs** (runbook now available)
4. **Live validation is critical** (can't improve without trade history)

---

## Appendix: File Reference

| File | Purpose |
|------|---------|
| `ALPACA_401_RUNBOOK.md` | Operator quick-fix guide (5 steps) |
| `docs/ALPACA_PAPER_ANALYSIS_GUIDE.md` | Performance review + 10 improvement levers |
| `src/msf_trader/swing/broker.py` | Credential handling + 401 diagnostics |
| `src/msf_trader/swing/rsi2.py` | Strategy parameters (RSI, SMA, hold time) |
| `src/msf_trader/swing/signals.py` | Entry/exit logic |
| `scripts/run_swing.sh` | Production scheduled run ($1000/trade, 10 slots) |

---

**Report Complete. MSF Trader paper trading is unblocked and ready for forward testing.**
