#!/usr/bin/env python3
"""Analyze baseline strategy to understand trade frequency and performance."""

import pandas as pd
from pathlib import Path
from msf_trader.swing import Rsi2Params, rsi2_trades, portfolio_backtest, DEFAULT_UNIVERSE

def analyze_per_instrument(data_dir="data/market", params=None):
    """Analyze per-instrument statistics."""
    params = params or Rsi2Params()
    data_dir = Path(data_dir)
    
    results = []
    for symbol in DEFAULT_UNIVERSE:
        path = data_dir / f"{symbol}_daily.csv"
        if not path.exists():
            continue
        
        df = pd.read_csv(path)
        df['date'] = pd.to_datetime(df['date'])
        
        trades = rsi2_trades(df, params, regime=True)
        
        if len(trades) > 0:
            wins = trades[trades['ret'] > 0]
            losses = trades[trades['ret'] <= 0]
            
            results.append({
                'symbol': symbol,
                'total_bars': len(df),
                'trades': len(trades),
                'trades_per_year': len(trades) / (len(df) / 252),
                'win_rate': len(wins) / len(trades) if len(trades) > 0 else 0,
                'avg_return': trades['ret'].mean() * 100,
                'avg_hold': trades['hold'].mean(),
                'wins': len(wins),
                'losses': len(losses),
            })
    
    results_df = pd.DataFrame(results)
    return results_df

def analyze_rsi_distribution(data_dir="data/market", params=None):
    """Analyze RSI(2) distribution to see how often we hit the threshold."""
    params = params or Rsi2Params()
    data_dir = Path(data_dir)
    
    from msf_trader.swing.rsi2 import wilder_rsi
    
    rsi_stats = []
    for symbol in DEFAULT_UNIVERSE[:5]:  # Sample first 5
        path = data_dir / f"{symbol}_daily.csv"
        if not path.exists():
            continue
        
        df = pd.read_csv(path)
        df['date'] = pd.to_datetime(df['date'])
        c = df['close']
        sma_200 = c.rolling(200).mean()
        rsi = wilder_rsi(c, 2)
        
        # Only consider when above 200-SMA (regime filter active)
        in_regime = c > sma_200
        rsi_in_regime = rsi[in_regime]
        
        rsi_stats.append({
            'symbol': symbol,
            'pct_below_10': (rsi_in_regime < 10).mean() * 100,
            'pct_below_15': (rsi_in_regime < 15).mean() * 100,
            'pct_below_20': (rsi_in_regime < 20).mean() * 100,
            'median_rsi': rsi_in_regime.median(),
            'days_in_regime': in_regime.sum(),
            'pct_in_regime': in_regime.mean() * 100,
        })
    
    return pd.DataFrame(rsi_stats)

if __name__ == '__main__':
    print("=" * 80)
    print("BASELINE STRATEGY ANALYSIS")
    print("=" * 80)
    
    # Current params
    baseline_params = Rsi2Params()
    print(f"\nBaseline Parameters:")
    print(f"  rsi_buy: {baseline_params.rsi_buy}")
    print(f"  regime_sma: {baseline_params.regime_sma}")
    print(f"  exit_sma: {baseline_params.exit_sma}")
    print(f"  max_hold: {baseline_params.max_hold}")
    print(f"  max_weight: {baseline_params.max_weight}")
    print(f"  cost_bps_rt: {baseline_params.cost_bps_rt}")
    
    # Portfolio backtest
    print("\n" + "-" * 80)
    print("PORTFOLIO BACKTEST (baseline)")
    print("-" * 80)
    res = portfolio_backtest(p=baseline_params)
    print(res.summary())
    print(f"\nTotal trades across portfolio: {sum(res.trades_per_instrument.values())}")
    print(f"Average trades per instrument: {sum(res.trades_per_instrument.values()) / len(res.trades_per_instrument):.1f}")
    
    # Per-instrument analysis
    print("\n" + "-" * 80)
    print("PER-INSTRUMENT STATISTICS")
    print("-" * 80)
    per_inst = analyze_per_instrument(params=baseline_params)
    print(per_inst.to_string(index=False))
    
    print(f"\nSummary:")
    print(f"  Total trades: {per_inst['trades'].sum()}")
    print(f"  Avg trades/year/instrument: {per_inst['trades_per_year'].mean():.1f}")
    print(f"  Portfolio win rate: {per_inst['wins'].sum() / per_inst['trades'].sum() * 100:.1f}%")
    print(f"  Avg hold period: {per_inst['avg_hold'].mean():.1f} days")
    
    # RSI distribution analysis
    print("\n" + "-" * 80)
    print("RSI(2) DISTRIBUTION ANALYSIS (sample of 5 symbols)")
    print("-" * 80)
    rsi_dist = analyze_rsi_distribution(params=baseline_params)
    print(rsi_dist.to_string(index=False))
    
    print("\n" + "=" * 80)
    print("KEY INSIGHTS:")
    print("=" * 80)
    print(f"1. RSI(2) < 10 occurs only {rsi_dist['pct_below_10'].mean():.2f}% of regime days")
    print(f"2. RSI(2) < 15 occurs {rsi_dist['pct_below_15'].mean():.2f}% of regime days")
    print(f"3. RSI(2) < 20 occurs {rsi_dist['pct_below_20'].mean():.2f}% of regime days")
    print(f"4. Median RSI when in uptrend: {rsi_dist['median_rsi'].mean():.1f}")
    print(f"5. Markets in uptrend {rsi_dist['pct_in_regime'].mean():.1f}% of the time")
    print("\nCONCLUSION: RSI < 10 is VERY RARE, limiting trade frequency significantly.")
