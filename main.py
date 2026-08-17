#!/usr/bin/env python3
"""
Polymarket Trading Bot — Entry point.

Usage:
    python main.py web                # Dashboard + bot on port 8080
    python main.py web --port 3000    # Custom port
    python main.py run                # Headless, runs forever
    python main.py run --cycles 5     # Run 5 cycles then stop
    python main.py scan               # Show current opportunities
    python main.py crypto             # Live BTC/ETH/SOL prices + model check
    python main.py hermes             # What Hermes has learned so far
    python main.py status             # Portfolio snapshot
    python main.py reset              # Wipe paper trading state
"""
import argparse
import logging
import os

from config import Config
from src.analysis.crypto_strategy import CryptoStrategy
from src.analysis.market_analyzer import MarketAnalyzer
from src.api.crypto_client import CryptoClient
from src.api.paper_trader import PaperTrader
from src.api.polymarket_client import PolymarketClient
from src.bot import PolymarketBot
from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def cmd_run(args):
    """Run the trading bot headlessly."""
    bot = PolymarketBot(Config())
    bot.run(cycles=args.cycles or None)


def cmd_web(args):
    """Launch the web dashboard."""
    from src.web import start_web
    config = Config()
    print(f"Dashboard: http://localhost:{args.port}")
    if config.DASHBOARD_PASSWORD:
        print(f"Login as '{config.DASHBOARD_USER}' with your DASHBOARD_PASSWORD.")
    else:
        print("No DASHBOARD_PASSWORD set — the dashboard is open to anyone "
              "who can reach this port.")
    print()
    start_web(port=args.port, auto_start_bot=True)


def cmd_scan(args):
    """Scan markets and display trading opportunities."""
    config = Config()
    client = PolymarketClient(config)
    crypto = CryptoClient() if config.CRYPTO_ENABLED else None
    strategy = CryptoStrategy(crypto, config) if crypto else None
    analyzer = MarketAnalyzer(client, config, strategy)

    print("Scanning Polymarket for trading opportunities...\n")
    markets = analyzer.scan_markets(limit=args.limit)
    print(f"Found {len(markets)} tradeable markets.\n")

    signals = analyzer.generate_signals(markets)
    print(f"Generated {len(signals)} trading signals:\n")

    for i, sig in enumerate(signals[:20], 1):
        print(f"  {i:2d}. [{sig.strategy:14s}] {sig.market_name[:55]}")
        print(f"      {sig.side} @ ${sig.current_price:.4f} | "
              f"Fair: ${sig.fair_value:.4f} | "
              f"Edge: {sig.edge * 100:.1f}% | "
              f"Confidence: {sig.confidence:.0%}\n")


def cmd_crypto(args):
    """Show live crypto prices and sanity-check the pricing model."""
    config = Config()
    crypto = CryptoClient()

    print("Live crypto data\n" + "─" * 52)
    snapshot = crypto.get_snapshot()
    if not snapshot:
        print("Could not reach any price feed. Check your connection.")
        return

    for symbol, data in snapshot.items():
        vol = (f"{data['annualized_vol']}% annualized vol"
               if data["annualized_vol"] is not None else "vol unavailable")
        print(f"  {symbol:4s} ${data['spot']:>12,.2f}   "
              f"{data['change_24h']:+6.2f}% 24h   {vol}")

    # Show what the model would say about a few example markets
    strategy = CryptoStrategy(crypto, config)
    btc = crypto.get_volatility("BTC")
    if not btc:
        return

    spot = btc["spot"]
    print(f"\nModel fair probabilities (BTC spot ${spot:,.0f})\n" + "─" * 52)
    from datetime import datetime, timedelta, timezone
    horizon = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    for multiple in (0.8, 0.9, 1.1, 1.25, 1.5):
        strike = round(spot * multiple, -3)
        question = f"Will Bitcoin reach ${strike:,.0f} by year end?"
        model = strategy.model_probability(question, horizon)
        if model:
            print(f"  Touch ${strike:>10,.0f} within 90d: "
                  f"{model['fair_probability']:.1%}")


def cmd_hermes(args):
    """Show what Hermes has learned."""
    from src.agents.hermes import HermesAgent
    agent = HermesAgent(Config())
    state = agent.get_state()
    summary = state["summary"]

    print("Hermes — learning agent\n" + "─" * 52)
    print(f"  Enabled:            {state['enabled']}")
    print(f"  Brain (Gemini key): {'yes' if state['has_brain'] else 'no'}")
    print(f"  Trades learned from {summary['total_trades_learned_from']}")
    print(f"  Win rate            {summary['win_rate']:.0%}")
    print(f"  Cumulative PnL      ${summary['cumulative_pnl']:.2f}")
    print(f"  Reflections         {summary['reflections']}")
    print(f"  Next reflection in  {state['reflect_due_in']} trades")

    if state["lessons"]:
        print(f"\nLessons ({len(state['lessons'])})\n" + "─" * 52)
        for lesson in state["lessons"]:
            print(f"  [{lesson['id']}] {lesson['action'].upper()} "
                  f"(confidence {lesson['confidence']:.0%}, "
                  f"{lesson['evidence_count']} trades)")
            print(f"        {lesson['text']}")
            print(f"        when: {lesson['when']}\n")
    else:
        print("\nNo lessons yet — Hermes needs closed trades to reflect on.")

    if state["strategy_stats"]:
        print("Strategy performance\n" + "─" * 52)
        for name, stats in state["strategy_stats"].items():
            print(f"  {name:16s} {stats['trades']:3d} trades  "
                  f"{stats['win_rate']:.0%} win  "
                  f"${stats['pnl']:+.2f} total")


def cmd_status(args):
    """Show current portfolio status."""
    # Imported here so a missing `rich` only breaks this one command
    from src.dashboard import print_dashboard
    print_dashboard(PaperTrader(Config.INITIAL_BALANCE), cycle=0)


def cmd_reset(args):
    """Reset the paper trading state."""
    removed = []
    for path in (os.path.join("data", "paper_state.json"),
                 os.path.join("data", "hermes", "memory.json")):
        if os.path.exists(path):
            if path.endswith("memory.json") and not args.include_hermes:
                continue
            os.remove(path)
            removed.append(path)
    if removed:
        print("Removed:", ", ".join(removed))
    else:
        print("Nothing to reset.")
    if not args.include_hermes:
        print("Hermes memory kept. Use --include-hermes to wipe it too.")


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run the trading bot headlessly")
    run_parser.add_argument("--cycles", type=int, default=None,
                            help="Number of cycles to run (default: infinite)")

    web_parser = sub.add_parser("web", help="Launch web dashboard")
    web_parser.add_argument("--port", type=int, default=8080,
                            help="Port for the web server (default: 8080)")

    scan_parser = sub.add_parser("scan", help="Scan for opportunities")
    scan_parser.add_argument("--limit", type=int, default=100,
                             help="Number of markets to scan")

    sub.add_parser("crypto", help="Live crypto prices and model check")
    sub.add_parser("hermes", help="Show what Hermes has learned")
    sub.add_parser("status", help="Show portfolio status")

    reset_parser = sub.add_parser("reset", help="Reset paper trading state")
    reset_parser.add_argument("--include-hermes", action="store_true",
                              help="Also wipe everything Hermes has learned")

    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)
    setup_logging()

    commands = {
        "run": cmd_run,
        "web": cmd_web,
        "scan": cmd_scan,
        "crypto": cmd_crypto,
        "hermes": cmd_hermes,
        "status": cmd_status,
        "reset": cmd_reset,
    }
    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return
    handler(args)


if __name__ == "__main__":
    main()
