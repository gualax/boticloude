"""Paper trading simulator — executes virtual trades against real market data."""
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


@dataclass
class Position:
    token_id: str
    market_name: str
    side: str          # "YES" or "NO"
    entry_price: float
    size: float        # number of shares
    cost: float        # total cost in USD
    timestamp: float = field(default_factory=time.time)
    current_price: float = 0.0

    @property
    def pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.size

    @property
    def pnl_pct(self) -> float:
        if self.cost == 0:
            return 0.0
        return self.pnl / self.cost * 100


@dataclass
class Trade:
    token_id: str
    market_name: str
    side: str
    action: str        # "buy" or "sell"
    price: float
    size: float
    cost: float
    timestamp: float = field(default_factory=time.time)
    pnl: float = 0.0


class PaperTrader:
    """Simulates trading with virtual money against live Polymarket prices."""

    def __init__(self, initial_balance: float = 1000.0, slippage_bps: int = 50):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, Position] = {}  # token_id -> Position
        self.trade_history: list[Trade] = []
        self.total_pnl = 0.0
        self.slippage_bps = slippage_bps  # basis points of simulated slippage
        self._load_state()

    # ── Trading operations ──────────────────────────────────────────────
    def _apply_slippage(self, price: float, is_buy: bool) -> float:
        """Apply simulated slippage to a trade price."""
        slippage = price * (self.slippage_bps / 10000)
        if is_buy:
            return min(price + slippage, 0.99)  # Buy at worse (higher) price
        return max(price - slippage, 0.01)      # Sell at worse (lower) price

    def buy(self, token_id: str, market_name: str, side: str,
            price: float, size: float) -> Optional[Trade]:
        """Buy shares of a market outcome."""
        price = self._apply_slippage(price, is_buy=True)
        cost = price * size
        if cost > self.balance:
            logger.warning("Insufficient balance: need $%.2f, have $%.2f",
                           cost, self.balance)
            return None

        self.balance -= cost

        if token_id in self.positions:
            pos = self.positions[token_id]
            total_cost = pos.cost + cost
            total_size = pos.size + size
            pos.entry_price = total_cost / total_size
            pos.size = total_size
            pos.cost = total_cost
        else:
            self.positions[token_id] = Position(
                token_id=token_id,
                market_name=market_name,
                side=side,
                entry_price=price,
                size=size,
                cost=cost,
            )

        trade = Trade(
            token_id=token_id,
            market_name=market_name,
            side=side,
            action="buy",
            price=price,
            size=size,
            cost=cost,
        )
        self.trade_history.append(trade)
        self._save_state()

        logger.info("PAPER BUY: %s %s @ $%.4f x %.1f = $%.2f",
                     market_name, side, price, size, cost)
        return trade

    def sell(self, token_id: str, size: Optional[float] = None) -> Optional[Trade]:
        """Sell (close) a position partially or fully."""
        if token_id not in self.positions:
            logger.warning("No position found for token %s", token_id)
            return None

        pos = self.positions[token_id]
        sell_size = size if size and size <= pos.size else pos.size
        sell_price = self._apply_slippage(pos.current_price, is_buy=False)
        revenue = sell_price * sell_size
        cost_basis = pos.entry_price * sell_size
        pnl = revenue - cost_basis

        self.balance += revenue
        self.total_pnl += pnl

        trade = Trade(
            token_id=token_id,
            market_name=pos.market_name,
            side=pos.side,
            action="sell",
            price=sell_price,
            size=sell_size,
            cost=revenue,
            pnl=pnl,
        )
        self.trade_history.append(trade)

        if sell_size >= pos.size:
            del self.positions[token_id]
        else:
            pos.size -= sell_size
            pos.cost = pos.entry_price * pos.size

        self._save_state()
        logger.info("PAPER SELL: %s @ $%.4f x %.1f | PnL: $%.2f",
                     pos.market_name, sell_price, sell_size, pnl)
        return trade

    def update_position_price(self, token_id: str, current_price: float):
        """Update the current price for a position."""
        if token_id in self.positions:
            self.positions[token_id].current_price = current_price

    # ── Portfolio metrics ───────────────────────────────────────────────
    @property
    def portfolio_value(self) -> float:
        positions_value = sum(
            p.current_price * p.size for p in self.positions.values()
        )
        return self.balance + positions_value

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.pnl for p in self.positions.values())

    @property
    def total_return_pct(self) -> float:
        if self.initial_balance == 0:
            return 0.0
        return ((self.portfolio_value - self.initial_balance)
                / self.initial_balance * 100)

    def get_summary(self) -> dict:
        return {
            "balance": round(self.balance, 2),
            "portfolio_value": round(self.portfolio_value, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "realized_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
        }

    # ── Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        state = {
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "total_pnl": self.total_pnl,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "trade_history": [asdict(t) for t in self.trade_history[-500:]],
        }
        with open(os.path.join(DATA_DIR, "paper_state.json"), "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self):
        path = os.path.join(DATA_DIR, "paper_state.json")
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                state = json.load(f)
            self.balance = state["balance"]
            self.initial_balance = state["initial_balance"]
            self.total_pnl = state["total_pnl"]
            self.positions = {
                k: Position(**v) for k, v in state["positions"].items()
            }
            self.trade_history = [Trade(**t) for t in state["trade_history"]]
            logger.info("Loaded paper trading state: balance=$%.2f, %d positions",
                        self.balance, len(self.positions))
        except Exception as e:
            logger.warning("Could not load paper state: %s", e)
