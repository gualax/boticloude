#!/usr/bin/env python3
"""
Polymarket Trading Bot — Entry point.

Usage:
    python main.py                    # Run bot continuously (paper trading)
    python main.py web                # Launch web dashboard on port 8080
    python main.py web --port 3000    # Launch on custom port
    python main.py --cycles 5         # Run 5 cycles then stop
    python main.py --scan             # Scan markets and show opportunities
    python main.py --status           # Show current portfolio status
    python main.py --reset            # Reset paper trading state
"""
import argparse
import logging
import os
import sys

from config import Config
from src.bot import PolymarketBot
from src.api.polymarket_client import PolymarketClient
from src.analysis.market_analyzer import MarketAnalyzer
from src.api.paper_trader import PaperTrader
from src.dashboard import print_dashboard
from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def cmd_run(args):
    """Run the trading bot."""
    config = Config()
    bot = PolymarketBot(config)
    cycles = args.cycles if args.cycles else None
    bot.run(cycles=cycles)


def cmd_scan(args):
    """Scan markets and display trading opportunities."""
    config = Config()
    client = PolymarketClient(config)
    analyzer = MarketAnalyzer(client)

    print("Scanning Polymarket for trading opportunities...\n")

    markets = analyzer.scan_markets(limit=args.limit)
    print(f"Found {len(markets)} tradeable markets.\n")

    signals = analyzer.generate_signals(markets)
    print(f"Generated {len(signals)} trading signals:\n")

    for i, sig in enumerate(signals[:20], 1):
        print(f"  {i:2d}. [{sig.strategy:15s}] {sig.market_name[:55]}")
        print(f"      {sig.side} @ ${sig.current_price:.4f} | "
              f"Fair: ${sig.fair_value:.4f} | "
              f"Edge: {sig.edge * 100:.1f}% | "
              f"Confidence: {sig.confidence:.0%}")
        print()


def cmd_status(args):
    """Show current portfolio status."""
    paper = PaperTrader(Config.INITIAL_BALANCE)
    print_dashboard(paper, cycle=0)


def cmd_web(args):
    """Launch the web dashboard."""
    from src.web import start_web
    print(f"Starting web dashboard on http://0.0.0.0:{args.port}")
    print("Bot will auto-start in the background.\n")
    start_web(port=args.port, auto_start_bot=True)


def cmd_reset(args):
    """Reset the paper trading state."""
    path = os.path.join("data", "paper_state.json")
    if os.path.exists(path):
        os.remove(path)
        print("Paper trading state reset successfully.")
    else:
        print("No existing state to reset.")


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # Run
    run_parser = sub.add_parser("run", help="Run the trading bot")
    run_parser.add_argument("--cycles", type=int, default=None,
                            help="Number of cycles to run (default: infinite)")

    # Scan
    scan_parser = sub.add_parser("scan", help="Scan for opportunities")
    scan_parser.add_argument("--limit", type=int, default=100,
                             help="Number of markets to scan")

    # Web dashboard
    web_parser = sub.add_parser("web", help="Launch web dashboard")
    web_parser.add_argument("--port", type=int, default=8080,
                            help="Port for the web server (default: 8080)")

    # Status
    sub.add_parser("status", help="Show portfolio status")

    # Reset
    sub.add_parser("reset", help="Reset paper trading state")

    args = parser.parse_args()

    # Setup
    os.makedirs("data", exist_ok=True)
    setup_logging()

    if args.command == "run" or args.command is None:
        cmd_run(args)
    elif args.command == "web":
        cmd_web(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "reset":
        cmd_reset(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
