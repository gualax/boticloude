"""Main bot orchestrator — coordinates scanning, analysis, trading, and risk."""
import logging
import time
from typing import Optional

from config import Config
from src.agents.hermes import HermesAgent
from src.analysis.crypto_strategy import CryptoStrategy
from src.analysis.daily_report import DailyAnalyzer
from src.analysis.market_analyzer import MarketAnalyzer, MarketSignal
from src.api.crypto_client import CryptoClient
from src.api.paper_trader import PaperTrader
from src.api.polymarket_client import PolymarketClient
from src.risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class PolymarketBot:
    """Trading bot that scans Polymarket, analyzes opportunities, and trades."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.client = PolymarketClient(self.config)

        # Bitcoin / crypto pricing module
        self.crypto = CryptoClient() if self.config.CRYPTO_ENABLED else None
        crypto_strategy = (
            CryptoStrategy(self.crypto, self.config) if self.crypto else None
        )

        self.analyzer = MarketAnalyzer(self.client, self.config, crypto_strategy)
        self.risk = RiskManager(self.config)
        self.paper = PaperTrader(
            self.config.INITIAL_BALANCE,
            slippage_bps=self.config.SLIPPAGE_BPS,
        )
        self.hermes = HermesAgent(self.config)
        self.daily_analyzer = DailyAnalyzer(self.config)
        self.is_running = False
        self.cycle_count = 0

    # ── Main cycle ──────────────────────────────────────────────────────
    def run_cycle(self) -> dict:
        """Execute one full trading cycle: scan → analyze → trade → manage risk."""
        self.cycle_count += 1
        self.risk.set_cycle(self.cycle_count)
        logger.info("═" * 60)
        logger.info("Starting trading cycle #%d", self.cycle_count)

        result = {
            "cycle": self.cycle_count,
            "signals_found": 0,
            "signals": [],
            "trades_executed": 0,
            "positions_closed": 0,
            "hermes_vetoes": 0,
            "errors": [],
        }

        self._update_positions()
        result["positions_closed"] = self._process_exits()

        # Scan for new opportunities
        try:
            markets = self.analyzer.scan_markets(limit=100)
            signals = self.analyzer.generate_signals(markets)
            result["signals_found"] = len(signals)
            # Handed back so callers (the dashboard) need not scan again
            result["signals"] = signals
        except Exception as e:
            logger.error("Error scanning markets: %s", e)
            result["errors"].append(str(e))
            return result

        executed, vetoed, errors = self._process_entries(signals)
        result["trades_executed"] = executed
        result["hermes_vetoes"] = vetoed
        result["errors"].extend(errors)

        summary = self.paper.get_summary()
        logger.info(
            "Cycle #%d complete | Portfolio: $%.2f | PnL: $%.2f (%.1f%%) | "
            "Positions: %d | Trades: %d | Hermes vetoes: %d",
            self.cycle_count,
            summary["portfolio_value"],
            summary["unrealized_pnl"] + summary["realized_pnl"],
            summary["total_return_pct"],
            summary["open_positions"],
            executed,
            vetoed,
        )

        self._run_learning_cycles()
        return result

    # ── Exits ───────────────────────────────────────────────────────────
    def _process_exits(self) -> int:
        """Close positions that hit stop-loss, take-profit or trailing stop."""
        to_close: list[tuple[str, str]] = []
        for token_id, pos in self.paper.positions.items():
            reason = None
            if self.risk.check_position_stop_loss(pos.entry_price,
                                                  pos.current_price):
                reason = "stop-loss"
            elif self.risk.check_take_profit(pos.entry_price, pos.current_price):
                reason = "take-profit"
            elif self.risk.check_trailing_stop(token_id, pos.entry_price,
                                               pos.current_price):
                reason = "trailing-stop"
            if reason:
                to_close.append((token_id, reason))

        closed = 0
        for token_id, reason in to_close:
            pos = self.paper.positions[token_id]
            logger.info("%s triggered for %s", reason.upper(),
                        pos.market_name[:50])

            if reason == "stop-loss" and pos.current_price < pos.entry_price:
                self.risk.add_cooldown(pos.market_name)

            trade = self.paper.sell(token_id)
            if trade:
                # Hermes learns from every completed round trip
                self.hermes.note_exit(token_id, pos, trade.price,
                                      trade.pnl, reason)
            self.risk.clear_trailing_data(token_id)
            closed += 1
        return closed

    # ── Entries ─────────────────────────────────────────────────────────
    def _process_entries(self, signals: list[MarketSignal]) -> tuple[int, int, list]:
        """Size, vet and execute new trades. Returns (executed, vetoed, errors)."""
        executed = 0
        vetoed = 0
        errors: list[str] = []

        for signal in signals:
            if not self.risk.validate_signal(signal):
                continue
            if signal.token_id in self.paper.positions:
                continue
            if self.risk.is_on_cooldown(signal.market_name):
                continue

            market_exposure = self.risk.check_market_exposure(
                signal.market_name, self.paper.positions)
            if market_exposure >= self.config.MAX_EXPOSURE_PER_MARKET:
                continue

            # Hermes has the final say before any money moves
            verdict = self.hermes.evaluate_signal(signal)
            if not verdict.approved:
                vetoed += 1
                logger.info("Hermes vetoed %s (%s): %s",
                            signal.market_name[:40], signal.strategy,
                            verdict.summary)
                continue

            size = self.risk.size_position(
                signal, self.paper.balance, len(self.paper.positions))
            if size <= 0:
                continue

            size = round(size * verdict.size_multiplier, 1)
            if size <= 0:
                continue
            if verdict.size_multiplier != 1.0:
                logger.info("Hermes resized %s to %.0f%%: %s",
                            signal.market_name[:40],
                            verdict.size_multiplier * 100, verdict.summary)

            trade_cost = signal.current_price * size

            # Never breach the per-market cap
            remaining = self.config.MAX_EXPOSURE_PER_MARKET - market_exposure
            if trade_cost > remaining:
                trade_cost = remaining
                size = round(trade_cost / signal.current_price, 1)
                if size <= 0:
                    continue

            current_exposure = sum(p.cost for p in self.paper.positions.values())
            if not self.risk.check_total_exposure(current_exposure, trade_cost):
                continue

            if self.config.PAPER_TRADING:
                trade = self.paper.buy(
                    token_id=signal.token_id,
                    market_name=signal.market_name,
                    side=signal.side,
                    price=signal.current_price,
                    size=size,
                )
            else:
                try:
                    trade = self.client.place_market_order(
                        signal.token_id, "buy", trade_cost)
                except Exception as e:
                    logger.error("Live trade failed: %s", e)
                    errors.append(str(e))
                    continue

            if trade:
                executed += 1
                self.hermes.note_entry(signal.token_id, signal, size)
                logger.info(
                    "Trade executed: %s | %s @ $%.4f | Edge: %.2f%% | "
                    "Strategy: %s",
                    signal.market_name[:50], signal.side,
                    signal.current_price, signal.edge * 100, signal.strategy,
                )
        return executed, vetoed, errors

    # ── Learning ────────────────────────────────────────────────────────
    def _run_learning_cycles(self):
        """Hermes reflection and the end-of-day report."""
        if self.hermes.should_reflect():
            try:
                self.hermes.reflect()
            except Exception as e:
                logger.error("Hermes reflection failed: %s", e)

        if self.daily_analyzer.should_run():
            try:
                report = self.daily_analyzer.run_analysis(self)
                if report and report.get("applied_changes"):
                    logger.info("Gemini auto-applied %d parameter changes",
                                len(report["applied_changes"]))
            except Exception as e:
                logger.error("Daily analysis failed: %s", e)

    # ── Loop ────────────────────────────────────────────────────────────
    def run(self, cycles: Optional[int] = None):
        """Run the bot continuously or for a fixed number of cycles."""
        self.is_running = True
        logger.info("Bot v%s started in %s mode", self.config.VERSION,
                    "PAPER TRADING" if self.config.PAPER_TRADING else "LIVE")
        logger.info("Initial balance: $%.2f", self.paper.initial_balance)
        if self.config.CRYPTO_ENABLED:
            logger.info("Crypto pricing module: ON")
        if self.config.HERMES_ENABLED:
            state = self.hermes.get_state()
            logger.info("Hermes: ON | %d lessons | %d trades learned from",
                        state["summary"]["lessons"],
                        state["summary"]["total_trades_learned_from"])

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
        for token_id in list(self.paper.positions):
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
        hermes = self.hermes.get_state()["summary"]
        logger.info("Hermes Lessons:    %d (from %d trades)",
                    hermes["lessons"], hermes["total_trades_learned_from"])
        logger.info("═" * 60)
