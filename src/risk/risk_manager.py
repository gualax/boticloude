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
        # Track peak prices for trailing stop-loss
        self.peak_prices: dict[str, float] = {}

    def size_position(self, signal: MarketSignal, balance: float,
                      open_positions: int) -> float:
        """Calculate the optimal position size for a signal using Kelly criterion."""
        if open_positions >= self.config.MAX_OPEN_POSITIONS:
            logger.info("Max open positions (%d) reached",
                        self.config.MAX_OPEN_POSITIONS)
            return 0

        # Kelly criterion: f* = (bp - q) / b
        if signal.current_price <= 0.02 or signal.current_price >= 0.98:
            return 0

        b = (1.0 / signal.current_price) - 1  # Odds
        p = signal.fair_value                   # Win probability
        q = 1 - p

        if b <= 0:
            return 0

        kelly = (b * p - q) / b

        # Use fractional Kelly for safety
        kelly = max(0, kelly * self.config.KELLY_FRACTION)

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

    def check_position_stop_loss(self, entry_price: float,
                                 current_price: float) -> bool:
        """Check if an individual position should be stopped out (15% loss)."""
        if entry_price == 0:
            return False
        loss_pct = (entry_price - current_price) / entry_price
        return loss_pct >= self.config.STOP_LOSS_PCT

    def check_take_profit(self, entry_price: float,
                          current_price: float) -> bool:
        """Check if a position has hit take-profit target."""
        if entry_price == 0:
            return False
        gain_pct = (current_price - entry_price) / entry_price
        return gain_pct >= self.config.TAKE_PROFIT_PCT

    def update_trailing_stop(self, token_id: str, current_price: float):
        """Update the peak price for trailing stop tracking."""
        if token_id not in self.peak_prices:
            self.peak_prices[token_id] = current_price
        elif current_price > self.peak_prices[token_id]:
            self.peak_prices[token_id] = current_price

    def check_trailing_stop(self, token_id: str, entry_price: float,
                            current_price: float) -> bool:
        """Check if trailing stop-loss is triggered.

        Only activates after position gains TRAILING_STOP_ACTIVATION from entry.
        Then triggers if price drops TRAILING_STOP_DISTANCE from peak.
        """
        if entry_price == 0:
            return False

        gain_from_entry = (current_price - entry_price) / entry_price
        # Only activate trailing stop after sufficient gain
        if gain_from_entry < self.config.TRAILING_STOP_ACTIVATION:
            return False

        peak = self.peak_prices.get(token_id, current_price)
        if peak == 0:
            return False

        drop_from_peak = (peak - current_price) / peak
        if drop_from_peak >= self.config.TRAILING_STOP_DISTANCE:
            logger.info("Trailing stop triggered for %s: peak=$%.4f, "
                        "current=$%.4f, drop=%.1f%%",
                        token_id[:16], peak, current_price, drop_from_peak * 100)
            return True
        return False

    def clear_trailing_data(self, token_id: str):
        """Remove trailing stop data when position is closed."""
        self.peak_prices.pop(token_id, None)

    def validate_signal(self, signal: MarketSignal) -> bool:
        """Validate that a signal meets minimum quality criteria."""
        if signal.edge < self.config.MIN_EDGE:
            return False
        if signal.liquidity < self.config.MIN_LIQUIDITY:
            return False
        if signal.confidence < 0.1:
            return False
        if signal.current_price <= 0.02 or signal.current_price >= 0.98:
            return False
        return True

    def check_total_exposure(self, current_exposure: float,
                             new_trade_cost: float) -> bool:
        """Check if adding a new trade would exceed total exposure limit."""
        return (current_exposure + new_trade_cost) <= self.config.MAX_TOTAL_EXPOSURE
