"""Risk management — controls position sizing, exposure limits, and stop-losses."""
import logging
from typing import Optional

from config import Config
from src.analysis.market_analyzer import MarketSignal

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces risk limits on all trading decisions."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def size_position(self, signal: MarketSignal, balance: float,
                      open_positions: int) -> float:
        """Calculate the optimal position size for a signal using Kelly criterion."""
        if open_positions >= self.config.MAX_OPEN_POSITIONS:
            logger.info("Max open positions (%d) reached",
                        self.config.MAX_OPEN_POSITIONS)
            return 0

        # Kelly criterion: f* = (bp - q) / b
        # b = odds, p = probability of win, q = 1-p
        if signal.current_price <= 0 or signal.current_price >= 1:
            return 0

        b = (1.0 / signal.current_price) - 1  # Odds
        p = signal.fair_value                   # Win probability
        q = 1 - p

        if b <= 0:
            return 0

        kelly = (b * p - q) / b

        # Use fractional Kelly (25%) for safety
        kelly = max(0, kelly * 0.25)

        # Apply confidence scaling
        kelly *= signal.confidence

        # Calculate dollar amount
        max_per_trade = balance * self.config.MAX_LOSS_PER_TRADE
        position_usd = min(kelly * balance, max_per_trade)

        # Cap at max position size
        position_usd = min(position_usd, self.config.MAX_POSITION_SIZE)

        # Ensure minimum trade size ($1)
        if position_usd < 1.0:
            return 0

        # Convert to shares
        shares = position_usd / signal.current_price

        logger.debug("Position size for %s: kelly=%.3f, usd=$%.2f, shares=%.1f",
                     signal.market_name[:40], kelly, position_usd, shares)
        return round(shares, 1)

    def check_portfolio_stop_loss(self, portfolio_value: float,
                                  initial_balance: float) -> bool:
        """Check if portfolio-level stop-loss has been triggered."""
        if initial_balance == 0:
            return False
        loss_pct = (initial_balance - portfolio_value) / initial_balance
        if loss_pct >= self.config.STOP_LOSS_PCT:
            logger.warning("STOP LOSS triggered: portfolio down %.1f%%",
                           loss_pct * 100)
            return True
        return False

    def check_position_stop_loss(self, entry_price: float,
                                 current_price: float) -> bool:
        """Check if an individual position should be stopped out."""
        if entry_price == 0:
            return False
        loss_pct = (entry_price - current_price) / entry_price
        # Stop out if position loses more than 30% of its value
        return loss_pct >= 0.30

    def validate_signal(self, signal: MarketSignal) -> bool:
        """Validate that a signal meets minimum quality criteria."""
        if signal.edge < self.config.MIN_EDGE:
            return False
        if signal.liquidity < self.config.MIN_LIQUIDITY:
            return False
        if signal.confidence < 0.1:
            return False
        if signal.current_price <= 0.02 or signal.current_price >= 0.98:
            # Avoid extreme prices (low edge, high slippage)
            return False
        return True

    def check_total_exposure(self, current_exposure: float,
                             new_trade_cost: float) -> bool:
        """Check if adding a new trade would exceed total exposure limit."""
        return (current_exposure + new_trade_cost) <= self.config.MAX_TOTAL_EXPOSURE
