"""Risk management — controls position sizing, exposure limits, and stop-losses."""
import logging
import time
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
        # Cooldown: market_name -> cycle_number when cooldown expires
        self._cooldowns: dict[str, int] = {}
        self._current_cycle = 0

    def set_cycle(self, cycle: int):
        """Update current cycle number for cooldown tracking."""
        self._current_cycle = cycle

    def add_cooldown(self, market_name: str):
        """Add a cooldown after closing a position at a loss."""
        expires = self._current_cycle + self.config.LOSS_COOLDOWN_CYCLES
        self._cooldowns[market_name] = expires
        logger.info("Cooldown set for '%s' until cycle #%d", market_name[:40], expires)

    def is_on_cooldown(self, market_name: str) -> bool:
        """Check if a market is on cooldown after a loss."""
        expires = self._cooldowns.get(market_name)
        if expires is None:
            return False
        if self._current_cycle >= expires:
            del self._cooldowns[market_name]
            return False
        return True

    def size_position(self, signal: MarketSignal, balance: float,
                      open_positions: int) -> float:
        """Calculate the optimal position size for a signal using Kelly criterion."""
        if open_positions >= self.config.MAX_OPEN_POSITIONS:
            logger.info("Max open positions (%d) reached",
                        self.config.MAX_OPEN_POSITIONS)
            return 0

        min_price = self.config.MIN_PRICE
        max_price = self.config.MAX_PRICE

        if signal.current_price <= min_price or signal.current_price >= max_price:
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

        # Scale down for cheap tokens — reduce size linearly below $0.20
        if signal.current_price < 0.20:
            cheap_factor = signal.current_price / 0.20  # e.g. $0.05 → 0.25x
            position_usd *= cheap_factor
            logger.debug("Cheap token scaling: price=$%.2f factor=%.2f",
                         signal.current_price, cheap_factor)

        # Ensure minimum trade size ($1)
        if position_usd < 1.0:
            return 0

        # Convert to shares
        shares = position_usd / signal.current_price

        logger.debug("Position size for %s: kelly=%.3f, usd=$%.2f, shares=%.1f",
                     signal.market_name[:40], kelly, position_usd, shares)
        return round(shares, 1)

    def check_market_exposure(self, market_name: str,
                              positions: dict) -> float:
        """Calculate current USD exposure to a given market question."""
        exposure = 0.0
        for pos in positions.values():
            if pos.market_name == market_name:
                exposure += pos.cost
        return exposure

    def check_position_stop_loss(self, entry_price: float,
                                 current_price: float) -> bool:
        """Check if an individual position should be stopped out."""
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
        """Check if trailing stop-loss is triggered."""
        if entry_price == 0:
            return False

        gain_from_entry = (current_price - entry_price) / entry_price
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

    def rejection_reason(self, signal: MarketSignal) -> Optional[str]:
        """Why this signal would be skipped, or None if it passes."""
        if signal.edge < self.config.MIN_EDGE:
            return (f"edge {signal.edge * 100:.1f}% < minimo "
                    f"{self.config.MIN_EDGE * 100:.0f}%")
        if signal.liquidity < self.config.MIN_LIQUIDITY:
            return (f"liquidez ${signal.liquidity:.0f} < minimo "
                    f"${self.config.MIN_LIQUIDITY:.0f}")
        if signal.confidence < 0.1:
            return f"confianza {signal.confidence:.0%} < 10%"
        if signal.current_price <= self.config.MIN_PRICE:
            return (f"precio ${signal.current_price:.3f} <= minimo "
                    f"${self.config.MIN_PRICE:.2f}")
        if signal.current_price >= self.config.MAX_PRICE:
            return (f"precio ${signal.current_price:.3f} >= maximo "
                    f"${self.config.MAX_PRICE:.2f}")
        return None

    def validate_signal(self, signal: MarketSignal) -> bool:
        """Validate that a signal meets minimum quality criteria."""
        return self.rejection_reason(signal) is None

    def check_total_exposure(self, current_exposure: float,
                             new_trade_cost: float) -> bool:
        """Check if adding a new trade would exceed total exposure limit."""
        return (current_exposure + new_trade_cost) <= self.config.MAX_TOTAL_EXPOSURE
