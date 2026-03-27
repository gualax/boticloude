"""Main bot orchestrator — coordinates scanning, analysis, trading, and risk."""
import logging
import time
from typing import Optional

from config import Config
from src.api.polymarket_client import PolymarketClient
from src.api.paper_trader import PaperTrader
from src.analysis.market_analyzer import MarketAnalyzer, MarketSignal
from src.risk.risk_manager import RiskManager
from src.analysis.daily_report import DailyAnalyzer

logger = logging.getLogger(__name__)


class PolymarketBot:
    """Trading bot that scans Polymarket, analyzes opportunities, and trades."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.client = PolymarketClient(self.config)
        self.analyzer = MarketAnalyzer(self.client, self.config)
        self.risk = RiskManager(self.config)
        self.paper = PaperTrader(
            self.config.INITIAL_BALANCE,
            slippage_bps=self.config.SLIPPAGE_BPS,
        )
        self.daily_analyzer = DailyAnalyzer(self.config)
        self.is_running = False
        self.cycle_count = 0

    def run_cycle(self) -> dict:
        """Execute one full trading cycle: scan → analyze → trade → manage risk."""
        self.cycle_count += 1
        logger.info("═" * 60)
        logger.info("Starting trading cycle #%d", self.cycle_count)

        result = {
            "cycle": self.cycle_count,
            "signals_found": 0,
            "trades_executed": 0,
            "positions_closed": 0,
            "errors": [],
        }

        # Step 1: Update existing position prices & trailing stops
        self._update_positions()

        # Step 2: Check exits — stop-loss, take-profit, trailing stop
        positions_to_close = []
        for token_id, pos in self.paper.positions.items():
            close_reason = None

            if self.risk.check_position_stop_loss(pos.entry_price, pos.current_price):
                close_reason = "stop-loss"
            elif self.risk.check_take_profit(pos.entry_price, pos.current_price):
                close_reason = "take-profit"
            elif self.risk.check_trailing_stop(token_id, pos.entry_price,
                                               pos.current_price):
                close_reason = "trailing-stop"

            if close_reason:
                positions_to_close.append((token_id, close_reason))

        for token_id, reason in positions_to_close:
            logger.info("%s triggered for %s", reason.upper(),
                        self.paper.positions[token_id].market_name[:50])
            self.paper.sell(token_id)
            self.risk.clear_trailing_data(token_id)
            result["positions_closed"] += 1

        # Step 3: Scan for new opportunities
        try:
            markets = self.analyzer.scan_markets(limit=100)
            signals = self.analyzer.generate_signals(markets)
            result["signals_found"] = len(signals)
        except Exception as e:
            logger.error("Error scanning markets: %s", e)
            result["errors"].append(str(e))
            return result

        # Step 4: Execute trades on valid signals
        for signal in signals:
            if not self.risk.validate_signal(signal):
                continue

            # Skip if we already have a position in this market
            if signal.token_id in self.paper.positions:
                continue

            # Check exposure limits
            current_exposure = sum(
                p.cost for p in self.paper.positions.values()
            )

            # Size the position
            size = self.risk.size_position(
                signal,
                self.paper.balance,
                len(self.paper.positions),
            )
            if size <= 0:
                continue

            trade_cost = signal.current_price * size
            if not self.risk.check_total_exposure(current_exposure, trade_cost):
                logger.info("Skipping trade: would exceed exposure limit")
                continue

            # Execute the trade (paper or live)
            if self.config.PAPER_TRADING:
                trade = self.paper.buy(
                    token_id=signal.token_id,
                    market_name=signal.market_name,
                    side=signal.side,
                    price=signal.current_price,
                    size=size,
                )
            else:
                # Live trading
                try:
                    trade = self.client.place_market_order(
                        signal.token_id, "buy", trade_cost
                    )
                except Exception as e:
                    logger.error("Live trade failed: %s", e)
                    result["errors"].append(str(e))
                    continue

            if trade:
                result["trades_executed"] += 1
                logger.info(
                    "Trade executed: %s | %s @ $%.4f | Edge: %.2f%% | "
                    "Strategy: %s",
                    signal.market_name[:50],
                    signal.side,
                    signal.current_price,
                    signal.edge * 100,
                    signal.strategy,
                )

        # Log cycle summary
        summary = self.paper.get_summary()
        logger.info(
            "Cycle #%d complete | Portfolio: $%.2f | PnL: $%.2f (%.1f%%) | "
            "Positions: %d | Trades this cycle: %d",
            self.cycle_count,
            summary["portfolio_value"],
            summary["unrealized_pnl"] + summary["realized_pnl"],
            summary["total_return_pct"],
            summary["open_positions"],
            result["trades_executed"],
        )

        # Step 6: Daily AI self-analysis (runs once per day)
        if self.daily_analyzer.should_run():
            try:
                self.daily_analyzer.run_analysis(self)
            except Exception as e:
                logger.error("Daily analysis failed: %s", e)

        return result

    def run(self, cycles: Optional[int] = None):
        """Run the bot continuously or for a fixed number of cycles."""
        self.is_running = True
        logger.info("Bot started in %s mode",
                     "PAPER TRADING" if self.config.PAPER_TRADING else "LIVE")
        logger.info("Initial balance: $%.2f", self.paper.initial_balance)

        count = 0
        try:
            while self.is_running:
                self.run_cycle()
                count += 1

                if cycles and count >= cycles:
                    logger.info("Completed %d cycles, stopping.", cycles)
                    break

                logger.info("Sleeping %ds until next cycle...",
                            self.config.REBALANCE_INTERVAL)
                time.sleep(self.config.REBALANCE_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        finally:
            self.is_running = False
            self._print_final_report()

    def _update_positions(self):
        """Update current prices for all open positions and trailing stops."""
        for token_id, pos in self.paper.positions.items():
            try:
                mid = self.client.get_midpoint(token_id)
                if mid is not None:
                    self.paper.update_position_price(token_id, mid)
                    self.risk.update_trailing_stop(token_id, mid)
            except Exception as e:
                logger.warning("Failed to update price for %s: %s",
                               token_id[:16], e)

    def _print_final_report(self):
        """Print a final performance report."""
        summary = self.paper.get_summary()
        logger.info("═" * 60)
        logger.info("FINAL REPORT")
        logger.info("═" * 60)
        logger.info("Initial Balance:  $%.2f", self.paper.initial_balance)
        logger.info("Final Portfolio:   $%.2f", summary["portfolio_value"])
        logger.info("Cash Balance:      $%.2f", summary["balance"])
        logger.info("Unrealized PnL:    $%.2f", summary["unrealized_pnl"])
        logger.info("Realized PnL:      $%.2f", summary["realized_pnl"])
        logger.info("Total Return:      %.2f%%", summary["total_return_pct"])
        logger.info("Total Trades:      %d", summary["total_trades"])
        logger.info("Open Positions:    %d", summary["open_positions"])
        logger.info("═" * 60)
