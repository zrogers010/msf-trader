#!/usr/bin/env python3
"""Parameter sweep to find optimal settings for trade frequency and risk-adjusted returns."""

import pandas as pd
from msf_trader.swing import Rsi2Params, portfolio_backtest
import itertools

def sweep_parameters():
    """Test different parameter combinations."""
    
    # Parameters to sweep
    rsi_thresholds = [5, 10, 15, 20, 25]
    exit_smas = [5, 10, 15]
    max_holds = [5, 10, 15, 20]
    
    results = []
    
    print("Running parameter sweep...")
    print("This will take a few minutes...\n")
    
    total = len(rsi_thresholds) * len(exit_smas) * len(max_holds)
    count = 0
    
    for rsi_buy, exit_sma, max_hold in itertools.product(rsi_thresholds, exit_smas, max_holds):
        count += 1
        if count % 10 == 0:
            print(f"Progress: {count}/{total} ({100*count/total:.0f}%)")
        
        try:
            params = Rsi2Params(
                rsi_buy=rsi_buy,
                exit_sma=exit_sma,
                max_hold=max_hold
            )
            
            res = portfolio_backtest(p=params)
            
            total_trades = sum(res.trades_per_instrument.values())
            
            results.append({
                'rsi_buy': rsi_buy,
                'exit_sma': exit_sma,
                'max_hold': max_hold,
                'cagr': res.cagr * 100,
                'sharpe': res.sharpe,
                'max_dd': res.max_drawdown * 100,
                'exposure': res.exposure * 100,
                'total_trades': total_trades,
                'trades_per_year': total_trades / 23,  # 23 years of data
                'score': res.sharpe * res.cagr * 100,  # Combined score
            })
        except Exception as e:
            print(f"Error with params {rsi_buy}/{exit_sma}/{max_hold}: {e}")
    
    df = pd.DataFrame(results)
    
    # Find best by different metrics
    print("\n" + "=" * 100)
    print("TOP 10 BY SHARPE RATIO")
    print("=" * 100)
    top_sharpe = df.nlargest(10, 'sharpe')
    print(top_sharpe.to_string(index=False))
    
    print("\n" + "=" * 100)
    print("TOP 10 BY CAGR")
    print("=" * 100)
    top_cagr = df.nlargest(10, 'cagr')
    print(top_cagr.to_string(index=False))
    
    print("\n" + "=" * 100)
    print("TOP 10 BY COMBINED SCORE (Sharpe * CAGR)")
    print("=" * 100)
    top_score = df.nlargest(10, 'score')
    print(top_score.to_string(index=False))
    
    print("\n" + "=" * 100)
    print("TOP 10 BY TRADE FREQUENCY")
    print("=" * 100)
    top_trades = df.nlargest(10, 'total_trades')
    print(top_trades.to_string(index=False))
    
    # Filter for "good" strategies (Sharpe > 0.6, CAGR > 5%, MaxDD < -25%)
    good = df[(df['sharpe'] > 0.6) & (df['cagr'] > 5) & (df['max_dd'] > -25)]
    
    print("\n" + "=" * 100)
    print(f"GOOD STRATEGIES (Sharpe>0.6, CAGR>5%, MaxDD>-25%): {len(good)} out of {len(df)}")
    print("=" * 100)
    print(good.sort_values('score', ascending=False).to_string(index=False))
    
    # Save results
    df.to_csv('data/parameter_sweep_results.csv', index=False)
    print(f"\n\nFull results saved to data/parameter_sweep_results.csv")
    
    return df

if __name__ == '__main__':
    results_df = sweep_parameters()
