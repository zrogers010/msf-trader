# MSF Trader: Alpaca 401 Fix Runbook

**Issue:** MSF Trader paper trading blocked by `HTTP 401 Unauthorized` on `/v2/clock`  
**Root Cause:** Invalid/revoked Alpaca paper trading credentials  
**Fix Time:** ~5 minutes

---

## Diagnosis Confirmed

Operator tested paper key `PKAK7...` with curl:
```bash
curl -H "APCA-API-KEY-ID: PKAK7..." \
  https://paper-api.alpaca.markets/v2/clock
# Result: 401 Unauthorized
```

**Conclusion:** The keys themselves are invalid/revoked, not a code bug.

---

## Immediate Fix (5 Steps)

### 1. Generate Fresh Paper Trading Keys

1. Log in: https://app.alpaca.markets/
2. Navigate: **Paper Trading** (left sidebar) → **API Keys** tab
3. Click: **Regenerate Keys**
4. Copy **both** values:
   - Key ID (e.g. `PKAK7NQP2...`)
   - Secret Key (long hex string - **only shown once**)

### 2. Update `.env` File

```bash
# SSH to Mac Mini
cd /Users/zach/code/src/msf-trader

# Edit .env
nano .env
```

Set these lines (replace with actual values from step 1):
```bash
ALPACA_PAPER_KEY_ID=PKAK7NQP2BLAHBLAHBLAH
ALPACA_PAPER_SECRET_KEY=your_actual_secret_here_no_spaces
```

**Important:**
- No spaces around `=`
- Copy secret **exactly** (common mistake: trailing space or truncation)
- These are DIFFERENT from `APCA_API_KEY_ID` (data keys)

### 3. Test with Curl

```bash
# Load new vars
source .env

# Test paper API (should return JSON with "is_open": true/false)
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/clock
```

**If still 401:**
- Regenerate keys again (Alpaca UI caching issue)
- Double-check secret has no spaces/truncation
- Verify you're in **Paper Trading** section (not Live or Data)

**If returns JSON:** ✅ Keys are valid, proceed to step 4

### 4. Test MSF Trader

```bash
source .venv/bin/activate
msf-trader swing-plan --broker alpaca --dollars 100 --slots 1
```

**Expected:** Prints order plan (or "No orders today") with **no 401 error**

**If 401 persists:**
- Check `.env` was saved: `grep ALPACA_PAPER_KEY_ID .env`
- Restart shell: `exec $SHELL` then `cd /Users/zach/code/src/msf-trader`
- Re-source: `source .venv/bin/activate`

### 5. Scheduled Job (No Action Needed)

The launchd job sources the venv on each run, which loads `.env`. New keys will be used automatically on the next scheduled run (Mon-Fri 15:45 ET).

**Verify:** Check tomorrow's log for no 401:
```bash
tail -f logs/swing-$(date +%Y%m%d).log
```

---

## Environment Variables Reference

MSF Trader expects these in `.env`:

| Variable | Purpose | Generate At |
|----------|---------|-------------|
| `ALPACA_PAPER_KEY_ID` | Paper trading | Paper Trading → API Keys |
| `ALPACA_PAPER_SECRET_KEY` | Paper trading | Paper Trading → API Keys |
| `APCA_API_KEY_ID` | Market data (backtests) | Market Data → API Keys |
| `APCA_API_SECRET_KEY` | Market data | Market Data → API Keys |

**Do NOT mix:** Paper keys ≠ Data keys. Generate separate pairs.

---

## How MSF Trader Reads Credentials

```python
# Prefers paper-specific keys:
key = os.environ.get("ALPACA_PAPER_KEY_ID") or os.environ.get("APCA_API_KEY_ID")
secret = os.environ.get("ALPACA_PAPER_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
```

**Fallback behavior:** If `ALPACA_PAPER_KEY_ID` is missing, it uses `APCA_API_KEY_ID` (data keys) → 401 because data keys don't have trading permissions.

**Fix:** Always set `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET_KEY` for paper trading.

---

## Troubleshooting

### "Still getting 401 after regenerating keys"
- Copy secret again (no spaces before/after)
- Use a different browser/incognito at alpaca.markets (cache issue)
- Verify account: keys from Account A won't work for Account B's paper

### "Keys worked yesterday, broke today"
- Alpaca may have revoked keys (security policy)
- Check email from Alpaca for revocation notice
- Regenerate fresh keys

### "How do I know if I'm using the right keys?"
```bash
# Paper keys start with PK (but so do data keys!)
echo $ALPACA_PAPER_KEY_ID  # should show PKAK7... or similar

# Test directly:
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/account
# Should return account summary JSON, not 401
```

---

## Post-Fix: Performance Review

Once trading resumes, analyze performance:

1. **Pull historical fills:**
   ```bash
   curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
        -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
        "https://paper-api.alpaca.markets/v2/orders?status=closed&limit=500" \
        > data/alpaca_order_history.json
   ```

2. **Check equity curve:**
   ```bash
   curl ... \
     "https://paper-api.alpaca.markets/v2/account/portfolio/history?period=1M&timeframe=1D"
   ```

3. **Analyze performance:**
   - Win rate, avg hold time, realized P&L
   - Compare live fills vs backtest expectations
   - Identify slippage on market orders

4. **Test improvements:**
   See `docs/ALPACA_PAPER_ANALYSIS_GUIDE.md` for 10 ranked improvement levers (RSI threshold, exit SMA, max hold, etc.)

---

## Summary

**Root cause:** Bad Alpaca paper keys in `.env`  
**Fix:** Regenerate at alpaca.markets → update `.env` → test with curl → test with MSF Trader  
**Time:** 5 minutes  
**No code changes needed**

Updated PR #1 includes better error messages for future 401s.
