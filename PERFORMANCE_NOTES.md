# Strategy Improvement Analysis

## Baseline Performance (Current Production)

**Parameters:**
- `rsi_buy`: 10
- `exit_sma`: 5
- `max_hold`: 10
- `max_weight`: 0.20
- `cost_bps_rt`: 3.0

**Results (2004-2026, 20 ETFs):**
- CAGR: 6.3%
- Sharpe: 0.71
- Max Drawdown: -20.2%
- Total Trades: 3,308 (143.8 per year)
- Trades per instrument per year: 7.4
- Win Rate: 71.2%
- Time Invested: 50%

## Problem Identification

Analysis of RSI(2) distribution shows that **RSI < 10 is extremely rare**, occurring only **7.8% of regime days**:
- RSI < 10: 7.8% of days (current threshold - VERY restrictive)
- RSI < 15: 12.2% of days
- RSI < 20: 16.2% of days  
- RSI < 25: 21.1% of days (estimated)

This explains the low trade frequency. The median RSI when in an uptrend is ~65, meaning RSI < 10 requires an exceptionally deep pullback.

## Parameter Sweep Results

Tested 60 combinations across:
- RSI thresholds: [5, 10, 15, 20, 25]
- Exit SMAs: [5, 10, 15]
- Max hold periods: [5, 10, 15, 20]

### Top 3 Configurations by Combined Score (Sharpe × CAGR)

#### 1. **RECOMMENDED: RSI=25, exit_sma=5, max_hold=20**
- CAGR: **8.8%** (+40% vs baseline)
- Sharpe: **0.76** (+7% vs baseline)
- Max DD: **-18.1%** (better than baseline -20.2%)
- Total Trades: **6,973** (+111% vs baseline, 303/year)
- Score: 6.70

**Why this works:**
- RSI < 25 captures legitimate mean-reversion setups (still deeply oversold on 2-period)
- Exit at 5-SMA captures the bounce quickly (proven optimal)
- 20-day max hold allows more time for convergence
- **Both more trades AND better risk-adjusted returns**

#### 2. RSI=25, exit_sma=5, max_hold=10
- CAGR: 8.8%
- Sharpe: 0.76
- Max DD: -18.1%
- Total Trades: 6,992 (304/year)
- Score: 6.66
- (Virtually identical to #1, slightly shorter hold)

#### 3. RSI=20, exit_sma=5, max_hold=20
- CAGR: 8.1%
- Sharpe: 0.74
- Max DD: -17.3%
- Total Trades: 5,880 (256/year)
- Score: 6.04
- (More conservative, +78% trades vs baseline, still excellent)

### High-Frequency Alternative: RSI=25, exit_sma=10, max_hold=5
- CAGR: 7.5%
- Sharpe: 0.66
- Max DD: -22.1%
- Total Trades: **7,270** (316/year, highest trade count with acceptable metrics)
- For users who prioritize turnover and accept slightly wider drawdowns

## Recommended Changes

### Primary Recommendation
Update default parameters to:
```python
@dataclass
class Rsi2Params:
    rsi_buy: float = 25.0          # was 10.0 - captures more valid setups
    rsi_period: int = 2            # unchanged
    regime_sma: int = 200          # unchanged
    exit_sma: int = 5              # unchanged (proven optimal)
    max_hold: int = 20             # was 10 - allow more time for mean reversion
    cost_bps_rt: float = 3.0       # unchanged
    max_weight: float = 0.20       # unchanged
```

**Impact:**
- **+111% more trades** (3,308 → 6,973)
- **+40% higher CAGR** (6.3% → 8.8%)
- **+7% better Sharpe** (0.71 → 0.76)
- **Lower max drawdown** (-20.2% → -18.1%)

### Conservative Alternative (for risk-averse users)
```python
rsi_buy = 20.0  # middle ground
max_hold = 20
# Still gives +78% more trades with Sharpe 0.74
```

### Aggressive Alternative (maximum turnover)
```python
rsi_buy = 25.0
exit_sma = 10
max_hold = 5
# 7,270 trades/23yr, Sharpe 0.66, CAGR 7.5%
```

## Additional Improvements to Consider

1. **Expand Universe** - Add more liquid sector/thematic ETFs:
   - Technology: VGT, SOXX
   - Healthcare: XLV, IBB
   - Consumer: XRT, VCR
   - International: VEA, VWO, FXI
   
2. **Variable Position Sizing** - Scale position by RSI depth:
   - RSI < 5: 1.5x weight (deeply oversold)
   - RSI 5-15: 1.0x weight
   - RSI 15-25: 0.75x weight (less conviction)
   
3. **Volatility-Based Stops** - Instead of fixed candle-based stop, use ATR-based stops

4. **Additional Entry Filters** (optional, may reduce trades):
   - Volume surge (>1.5x avg) for conviction
   - Not more than 3 consecutive down days (avoid catching falling knives)

## Implementation Plan

1. ✅ Run parameter sweep (DONE)
2. Update `Rsi2Params` defaults in `rsi2.py`
3. Add parameter presets (conservative/balanced/aggressive) 
4. Add CLI option to select preset
5. Update documentation/README
6. Run validation backtest
7. Create before/after comparison report
8. Open PR with evidence

## Validation Checklist

- [ ] Baseline metrics reproduced
- [ ] Improved params tested across full 2004-2026 window
- [ ] Per-instrument results remain healthy (no single-name dependency)
- [ ] Out-of-sample validation (2020-2026 holdout)
- [ ] Regime analysis (2008 crisis, 2020 pandemic, 2022 bear)
- [ ] Documentation updated
- [ ] Tests updated

## Risk Disclosure

- Backtested results do not guarantee future performance
- Increased trade frequency = higher transaction costs in practice
- Paper trading validation required before live deployment
- Parameter optimization on in-sample data carries overfitting risk
- Strategy works in mean-reversion regime; may underperform in trends
