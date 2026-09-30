# MSF Trader: Alpaca 401 Fix Runbook

**Issue:** MSF Trader paper trading blocked by `HTTP 401 Unauthorized`  
**Root Cause:** Invalid/revoked paper trading keys for the intended account  
**Fix Time:** ~5 minutes

---

## Fix (3 Steps)

### 1. Regenerate Paper Keys for Your Account

1. Log in: https://app.alpaca.markets/
2. Navigate: **Paper Trading** → **API Keys**
3. Click: **Regenerate Keys**
4. Copy both values (Key ID + Secret)

**Important:** Generate keys for the paper account you're actually trading on. Do NOT copy keys from a different Alpaca account.

### 2. Update `.env`

```bash
ALPACA_PAPER_KEY_ID=<your_new_key_id>
ALPACA_PAPER_SECRET_KEY=<your_new_secret>
```

No spaces around `=`. Copy secret exactly (shown only once).

### 3. Test

```bash
# Quick API test
curl -H "APCA-API-KEY-ID: $ALPACA_PAPER_KEY_ID" \
     -H "APCA-API-SECRET-KEY: $ALPACA_PAPER_SECRET_KEY" \
     https://paper-api.alpaca.markets/v2/clock

# MSF Trader test
msf-trader swing-plan --broker alpaca --dollars 100 --slots 1
```

Expected: JSON response (not 401).

---

## Key Points

- **Paper keys** (`ALPACA_PAPER_*`) access `paper-api.alpaca.markets`
- **Data keys** (`APCA_API_*`) access `data.alpaca.markets` only
- Each Alpaca account needs its own keys — don't mix
- MSF Trader prefers `ALPACA_PAPER_*`, falls back to `APCA_API_*` (prints warning)

---

## Troubleshooting

**Still 401?**
- Regenerate keys again (Alpaca UI cache)
- Verify account: keys must match the paper account you're using
- Check `.env` has no spaces/truncation

**Wrong account?**
- Check account ID in Alpaca dashboard
- Confirm `.env` keys match that account

---

**Summary:** Regenerate paper keys for the intended account → update `.env` → test. No code changes needed. PR #1 adds better 401 diagnostics.
