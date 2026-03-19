"""Client for Polymarket CLOB and Gamma APIs."""
import logging
import time
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Wrapper around Polymarket REST APIs for market data and trading."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.clob_url = self.config.CLOB_API_URL
        self.gamma_url = self.config.GAMMA_API_URL
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._clob_client = None

    # ── Live CLOB client (only when not paper trading) ──────────────────
    def _init_clob_client(self):
        """Initialize the official py-clob-client for authenticated operations."""
        if self._clob_client is not None:
            return
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self.config.API_KEY,
                api_secret=self.config.API_SECRET,
                api_passphrase=self.config.API_PASSPHRASE,
            )
            self._clob_client = ClobClient(
                self.clob_url,
                key=self.config.PRIVATE_KEY,
                chain_id=137,  # Polygon mainnet
                creds=creds,
            )
            logger.info("CLOB client initialized successfully")
        except Exception as e:
            logger.error("Failed to initialize CLOB client: %s", e)
            raise

    # ── Public market data (no auth required) ───────────────────────────
    def get_markets(self, limit: int = 100, offset: int = 0,
                    active: bool = True, closed: bool = False) -> list[dict]:
        """Fetch markets from Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        resp = self._get(f"{self.gamma_url}/markets", params=params)
        return resp if isinstance(resp, list) else []

    def get_market(self, condition_id: str) -> dict:
        """Fetch a single market by condition ID."""
        return self._get(f"{self.gamma_url}/markets/{condition_id}")

    def get_events(self, limit: int = 50, active: bool = True) -> list[dict]:
        """Fetch events (groups of markets)."""
        params = {"limit": limit, "active": str(active).lower()}
        resp = self._get(f"{self.gamma_url}/events", params=params)
        return resp if isinstance(resp, list) else []

    def get_orderbook(self, token_id: str) -> dict:
        """Get the orderbook for a specific token."""
        return self._get(f"{self.clob_url}/book", params={"token_id": token_id})

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get the midpoint price for a token."""
        try:
            resp = self._get(f"{self.clob_url}/midpoint",
                             params={"token_id": token_id})
            return float(resp.get("mid", 0))
        except Exception:
            return None

    def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """Get the best price for a token (buy or sell side)."""
        try:
            resp = self._get(f"{self.clob_url}/price",
                             params={"token_id": token_id, "side": side})
            return float(resp.get("price", 0))
        except Exception:
            return None

    def get_prices_history(self, token_id: str, interval: str = "1d",
                           fidelity: int = 60) -> list[dict]:
        """Get price history for a token."""
        params = {
            "market": token_id,
            "interval": interval,
            "fidelity": fidelity,
        }
        resp = self._get(f"{self.clob_url}/prices-history", params=params)
        return resp.get("history", []) if isinstance(resp, dict) else []

    def get_spread(self, token_id: str) -> Optional[dict]:
        """Get bid-ask spread for a token."""
        try:
            resp = self._get(f"{self.clob_url}/spread",
                             params={"token_id": token_id})
            return resp
        except Exception:
            return None

    # ── Authenticated trading (live mode) ───────────────────────────────
    def place_market_order(self, token_id: str, side: str,
                           amount: float) -> dict:
        """Place a market order via the CLOB client."""
        self._init_clob_client()
        from py_clob_client.clob_types import MarketOrderArgs
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
        )
        if side.lower() == "buy":
            return self._clob_client.create_and_post_market_order(order_args)
        else:
            return self._clob_client.create_and_post_market_order(order_args)

    def place_limit_order(self, token_id: str, side: str,
                          price: float, size: float) -> dict:
        """Place a limit order via the CLOB client."""
        self._init_clob_client()
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
        )
        order_side = BUY if side.lower() == "buy" else SELL
        signed_order = self._clob_client.create_order(order_args, order_side)
        return self._clob_client.post_order(signed_order)

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an existing order."""
        self._init_clob_client()
        return self._clob_client.cancel(order_id)

    def get_open_orders(self) -> list[dict]:
        """Get all open orders for the authenticated user."""
        self._init_clob_client()
        return self._clob_client.get_orders()

    # ── Internal helpers ────────────────────────────────────────────────
    def _get(self, url: str, params: Optional[dict] = None,
             retries: int = 3) -> dict | list:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.warning("GET %s attempt %d failed: %s",
                               url, attempt + 1, e)
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
        return {}
