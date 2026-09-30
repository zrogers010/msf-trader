"""Additional tests for strategy improvements."""

import pytest
from msf_trader.swing import Rsi2Params, portfolio_backtest
import pandas as pd
import numpy as np


def test_preset_parameters():
    """Test that presets have correct parameter values."""
    legacy = Rsi2Params.legacy()
    assert legacy.rsi_buy == 10.0
    assert legacy.max_hold == 10
    assert legacy.exit_sma == 5
    
    conservative = Rsi2Params.conservative()
    assert conservative.rsi_buy == 20.0
    assert conservative.max_hold == 20
    
    balanced = Rsi2Params.balanced()
    assert balanced.rsi_buy == 25.0
    assert balanced.max_hold == 20
    
    aggressive = Rsi2Params.aggressive()
    assert aggressive.rsi_buy == 25.0
    assert aggressive.exit_sma == 10
    assert aggressive.max_hold == 5


def test_default_params_are_balanced():
    """Verify new defaults match balanced preset."""
    default = Rsi2Params()
    balanced = Rsi2Params.balanced()
    
    assert default.rsi_buy == balanced.rsi_buy
    assert default.max_hold == balanced.max_hold
    assert default.exit_sma == balanced.exit_sma


def test_balanced_generates_more_trades_than_legacy():
    """Integration test: verify balanced preset produces more trades."""
    # This test requires data files, so we'll skip if not available
    from pathlib import Path
    data_dir = Path("data/market")
    
    if not (data_dir / "SPY_daily.csv").exists():
        pytest.skip("Test data not available")
    
    # Run small sample backtest
    legacy_res = portfolio_backtest(
        symbols=["SPY", "QQQ"],
        p=Rsi2Params.legacy(),
        data_dir=str(data_dir)
    )
    
    balanced_res = portfolio_backtest(
        symbols=["SPY", "QQQ"],
        p=Rsi2Params.balanced(),
        data_dir=str(data_dir)
    )
    
    legacy_trades = sum(legacy_res.trades_per_instrument.values())
    balanced_trades = sum(balanced_res.trades_per_instrument.values())
    
    # Balanced should have significantly more trades
    assert balanced_trades > legacy_trades * 1.5, \
        f"Expected balanced ({balanced_trades}) to have >50% more trades than legacy ({legacy_trades})"


def test_balanced_maintains_quality():
    """Verify balanced preset maintains quality metrics."""
    from pathlib import Path
    data_dir = Path("data/market")
    
    if not (data_dir / "SPY_daily.csv").exists():
        pytest.skip("Test data not available")
    
    # Use a smaller subset for testing, but with realistic expectations
    balanced_res = portfolio_backtest(
        symbols=["SPY", "QQQ", "DIA"],
        p=Rsi2Params.balanced(),
        data_dir=str(data_dir)
    )
    
    # Should maintain positive returns (relaxed for 3-symbol subset)
    assert balanced_res.cagr > 0.02, "CAGR should be > 2% (3-symbol subset)"
    
    # Should maintain reasonable Sharpe
    assert balanced_res.sharpe > 0.4, "Sharpe should be > 0.4"
    
    # Should not have excessive drawdown
    assert balanced_res.max_drawdown > -0.30, "MaxDD should be > -30%"
    
    # Verify more trades than legacy
    legacy_res = portfolio_backtest(
        symbols=["SPY", "QQQ", "DIA"],
        p=Rsi2Params.legacy(),
        data_dir=str(data_dir)
    )
    
    balanced_trades = sum(balanced_res.trades_per_instrument.values())
    legacy_trades = sum(legacy_res.trades_per_instrument.values())
    
    assert balanced_trades > legacy_trades, \
        f"Balanced ({balanced_trades}) should have more trades than legacy ({legacy_trades})"
