"""Tests for the paper trading simulator."""
import os
import sys
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.paper_trader import PaperTrader


@pytest.fixture
def trader():
    """Create a fresh paper trader for each test (no slippage for determinism)."""
    t = PaperTrader(initial_balance=1000.0, slippage_bps=0)
    # Clear any loaded state
    t.balance = 1000.0
    t.positions = {}
    t.trade_history = []
    t.total_pnl = 0.0
    return t


class TestPaperTrader:
    def test_initial_state(self, trader):
        assert trader.balance == 1000.0
        assert trader.portfolio_value == 1000.0
        assert len(trader.positions) == 0

    def test_buy(self, trader):
        trade = trader.buy("token1", "Will X happen?", "YES", 0.60, 10)
        assert trade is not None
        assert trade.action == "buy"
        assert trader.balance == 994.0  # 1000 - (0.60 * 10)
        assert "token1" in trader.positions
        assert trader.positions["token1"].size == 10

    def test_buy_insufficient_balance(self, trader):
        trade = trader.buy("token1", "Test", "YES", 0.50, 5000)
        assert trade is None  # 0.50 * 5000 = 2500 > 1000

    def test_sell(self, trader):
        trader.buy("token1", "Test Market", "YES", 0.50, 100)
        trader.update_position_price("token1", 0.70)
        trade = trader.sell("token1")
        assert trade is not None
        assert trade.pnl == 20.0  # (0.70 - 0.50) * 100
        assert "token1" not in trader.positions

    def test_portfolio_value(self, trader):
        trader.buy("token1", "Test", "YES", 0.50, 100)  # cost = 50
        trader.update_position_price("token1", 0.60)
        # balance = 950, position value = 0.60 * 100 = 60
        assert trader.portfolio_value == 1010.0

    def test_total_return(self, trader):
        trader.buy("token1", "Test", "YES", 0.50, 100)
        trader.update_position_price("token1", 0.60)
        assert trader.total_return_pct == pytest.approx(1.0, abs=0.1)

    def test_multiple_positions(self, trader):
        trader.buy("t1", "Market A", "YES", 0.40, 50)
        trader.buy("t2", "Market B", "NO", 0.30, 100)
        assert len(trader.positions) == 2
        assert trader.balance == 1000 - (0.40 * 50) - (0.30 * 100)

    def test_averaging_into_position(self, trader):
        trader.buy("t1", "Test", "YES", 0.40, 100)  # cost = 40
        trader.buy("t1", "Test", "YES", 0.60, 100)  # cost = 60
        pos = trader.positions["t1"]
        assert pos.size == 200
        assert pos.cost == 100.0
        assert pos.entry_price == pytest.approx(0.50)  # avg price


class TestRiskManager:
    def test_kelly_sizing(self):
        from src.risk.risk_manager import RiskManager
        from src.analysis.market_analyzer import MarketSignal

        rm = RiskManager()
        signal = MarketSignal(
            token_id="t1",
            market_name="Test",
            condition_id="c1",
            side="YES",
            current_price=0.50,
            fair_value=0.60,
            edge=0.10,
            confidence=0.7,
            strategy="test",
            liquidity=1000,
        )
        size = rm.size_position(signal, balance=1000, open_positions=0)
        assert size > 0
        assert size * signal.current_price <= 50  # max position size

    def test_validate_signal(self):
        from src.risk.risk_manager import RiskManager
        from src.analysis.market_analyzer import MarketSignal

        rm = RiskManager()

        # Good signal
        good = MarketSignal(
            token_id="t1", market_name="Test", condition_id="c1",
            side="YES", current_price=0.50, fair_value=0.60,
            edge=0.10, confidence=0.7, strategy="test", liquidity=1000,
        )
        assert rm.validate_signal(good) is True

        # Bad signal: low edge
        bad = MarketSignal(
            token_id="t1", market_name="Test", condition_id="c1",
            side="YES", current_price=0.50, fair_value=0.51,
            edge=0.01, confidence=0.7, strategy="test", liquidity=1000,
        )
        assert rm.validate_signal(bad) is False
