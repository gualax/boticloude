"""CLI dashboard for monitoring the bot in real-time."""
import logging
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from src.api.paper_trader import PaperTrader

console = Console()
logger = logging.getLogger(__name__)


def print_dashboard(paper: PaperTrader, signals: list = None,
                    cycle: int = 0):
    """Print a rich CLI dashboard showing bot status."""
    console.clear()

    # Header
    console.print(Panel(
        Text("POLYMARKET TRADING BOT", style="bold cyan", justify="center"),
        subtitle=f"Cycle #{cycle} | Paper Trading Mode",
    ))

    # Portfolio summary
    summary = paper.get_summary()
    portfolio_table = Table(title="Portfolio Summary", show_header=False,
                            border_style="cyan")
    portfolio_table.add_column("Metric", style="bold")
    portfolio_table.add_column("Value", justify="right")

    pnl_color = "green" if summary["total_return_pct"] >= 0 else "red"

    portfolio_table.add_row("Balance", f"${summary['balance']:.2f}")
    portfolio_table.add_row("Portfolio Value", f"${summary['portfolio_value']:.2f}")
    portfolio_table.add_row(
        "Unrealized PnL",
        Text(f"${summary['unrealized_pnl']:.2f}", style=pnl_color),
    )
    portfolio_table.add_row(
        "Realized PnL",
        Text(f"${summary['realized_pnl']:.2f}", style=pnl_color),
    )
    portfolio_table.add_row(
        "Total Return",
        Text(f"{summary['total_return_pct']:.2f}%", style=pnl_color),
    )
    portfolio_table.add_row("Total Trades", str(summary["total_trades"]))
    console.print(portfolio_table)

    # Open positions
    if paper.positions:
        pos_table = Table(title="Open Positions", border_style="yellow")
        pos_table.add_column("Market", max_width=45)
        pos_table.add_column("Side")
        pos_table.add_column("Entry", justify="right")
        pos_table.add_column("Current", justify="right")
        pos_table.add_column("Size", justify="right")
        pos_table.add_column("PnL", justify="right")
        pos_table.add_column("PnL %", justify="right")

        for pos in paper.positions.values():
            pnl_style = "green" if pos.pnl >= 0 else "red"
            pos_table.add_row(
                pos.market_name[:45],
                pos.side,
                f"${pos.entry_price:.4f}",
                f"${pos.current_price:.4f}",
                f"{pos.size:.1f}",
                Text(f"${pos.pnl:.2f}", style=pnl_style),
                Text(f"{pos.pnl_pct:.1f}%", style=pnl_style),
            )

        console.print(pos_table)
    else:
        console.print("[dim]No open positions[/dim]")

    # Signals
    if signals:
        sig_table = Table(title="Top Signals", border_style="green")
        sig_table.add_column("Market", max_width=40)
        sig_table.add_column("Side")
        sig_table.add_column("Price", justify="right")
        sig_table.add_column("Fair Value", justify="right")
        sig_table.add_column("Edge", justify="right")
        sig_table.add_column("Strategy")

        for sig in signals[:10]:
            sig_table.add_row(
                sig.market_name[:40],
                sig.side,
                f"${sig.current_price:.4f}",
                f"${sig.fair_value:.4f}",
                f"{sig.edge * 100:.1f}%",
                sig.strategy,
            )

        console.print(sig_table)

    # Recent trades
    if paper.trade_history:
        trade_table = Table(title="Recent Trades (Last 5)", border_style="magenta")
        trade_table.add_column("Market", max_width=35)
        trade_table.add_column("Action")
        trade_table.add_column("Price", justify="right")
        trade_table.add_column("Size", justify="right")
        trade_table.add_column("PnL", justify="right")

        for trade in paper.trade_history[-5:]:
            pnl_text = Text(f"${trade.pnl:.2f}",
                            style="green" if trade.pnl >= 0 else "red")
            trade_table.add_row(
                trade.market_name[:35],
                trade.action.upper(),
                f"${trade.price:.4f}",
                f"{trade.size:.1f}",
                pnl_text if trade.action == "sell" else Text("-"),
            )

        console.print(trade_table)
