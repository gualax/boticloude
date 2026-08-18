"""Client for Polymarket CLOB and Gamma APIs.

Polymarket sits behind Cloudflare and rate-limits aggressively. Two habits
keep this client from being cut off:

  - it identifies itself with a real User-Agent (a default
    `python-requests/x.y` agent gets connections reset), and
  - it paces its own requests and prefers batch endpoints, so a cycle
    costs a handful of calls rather than hundreds.
"""
import logging
import threading
import time
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Errors that mean "you are being throttled or blocked", not "bad request"
BLOCKED_STATUSES = {403, 429, 503}


class PolymarketClient:
    """Wrapper around Polymarket REST APIs for market data and trading."""

    def __init__(self, config: Optional[Config] = None,
                 min_interval: float = 0.25):
        self.config = config or Config()
        self.clob_url = self.config.CLOB_API_URL
        self.gamma_url = self.config.GAMMA_API_URL
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://polymarket.com",
            "Referer": "https://polymarket.com/",
        })
        self._clob_client = None

        # Self-imposed pacing: at most one request every `min_interval`
        self._min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()

        # Price history barely moves; re-fetching it every cycle is what
        # pushed this client over the rate limit in the first place.
        self._history_cache: dict[str, tuple[list, float]] = {}
        self._history_ttl = 1800  # 30 minutes

    def _throttle(self):
        """Space out requests so we stay under the rate limit."""
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.monotonic()

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
        except Exception as e:
            logger.debug("get_midpoint failed for %s: %s", token_id[:20], e)
            return None

    def get_midpoints(self, token_ids: list[str]) -> dict[str, float]:
        """Get midpoints for many tokens in one request.

        This is the difference between one call and a hundred, which is
        what keeps the client under Polymarket's rate limit. Falls back to
        individual lookups if the batch endpoint is unavailable.
        """
        if not token_ids:
            return {}

        body = [{"token_id": t} for t in token_ids]
        try:
            resp = self._post(f"{self.clob_url}/midpoints", json_body=body)
        except Exception as e:
            logger.warning("Batch midpoints failed (%s); falling back to "
                           "individual lookups", e)
            return self._midpoints_individually(token_ids)

        prices: dict[str, float] = {}
        # The API has returned both a mapping and a list over time
        if isinstance(resp, dict):
            for token_id, value in resp.items():
                try:
                    prices[token_id] = float(
                        value["mid"] if isinstance(value, dict) else value)
                except (TypeError, ValueError, KeyError):
                    continue
        elif isinstance(resp, list):
            for entry in resp:
                if not isinstance(entry, dict):
                    continue
                token_id = entry.get("token_id") or entry.get("asset_id")
                try:
                    if token_id is not None:
                        prices[token_id] = float(entry.get("mid", 0))
                except (TypeError, ValueError):
                    continue

        if not prices:
            return self._midpoints_individually(token_ids)
        return prices

    def _midpoints_individually(self, token_ids: list[str]) -> dict[str, float]:
        prices = {}
        for token_id in token_ids:
            mid = self.get_midpoint(token_id)
            if mid is not None:
                prices[token_id] = mid
        return prices

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
        """Get price history for a token, cached for 30 minutes.

        A week of hourly candles does not meaningfully change between
        five-minute cycles, so re-fetching it every cycle only burned
        rate limit.
        """
        key = f"{token_id}:{interval}:{fidelity}"
        cached = self._history_cache.get(key)
        if cached and (time.time() - cached[1]) < self._history_ttl:
            return cached[0]

        params = {
            "market": token_id,
            "interval": interval,
            "fidelity": fidelity,
        }
        try:
            resp = self._get(f"{self.clob_url}/prices-history", params=params)
        except Exception:
            # Serve stale data rather than losing the signal entirely
            return cached[0] if cached else []

        history = resp.get("history", []) if isinstance(resp, dict) else []
        self._history_cache[key] = (history, time.time())
        return history

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
        """Place a market order (FOK) via the CLOB client."""
        self._init_clob_client()
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY if side.lower() == "buy" else SELL,
        )
        signed_order = self._clob_client.create_market_order(order_args)
        return self._clob_client.post_order(signed_order, OrderType.FOK)

    def place_limit_order(self, token_id: str, side: str,
                          price: float, size: float) -> dict:
        """Place a limit order (GTC) via the CLOB client."""
        self._init_clob_client()
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side.lower() == "buy" else SELL,
        )
        signed_order = self._clob_client.create_order(order_args)
        return self._clob_client.post_order(signed_order, OrderType.GTC)

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
        return self._request("GET", url, params=params, retries=retries)

    def _post(self, url: str, json_body=None, retries: int = 3) -> dict | list:
        return self._request("POST", url, json_body=json_body, retries=retries)

    def _request(self, method: str, url: str, params: Optional[dict] = None,
                 json_body=None, retries: int = 3) -> dict | list:
        """Issue a paced request, backing off hard when we look throttled."""
        last_error: Optional[Exception] = None

        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body, timeout=20)

                # Being blocked or throttled deserves a real pause, not a
                # rapid retry that digs the hole deeper.
                if resp.status_code in BLOCKED_STATUSES:
                    wait = 5 * (attempt + 1)
                    logger.warning(
                        "%s %s -> HTTP %d (throttled); esperando %ds",
                        method, url, resp.status_code, wait)
                    last_error = requests.HTTPError(
                        f"HTTP {resp.status_code}", response=resp)
                    if attempt < retries - 1:
                        time.sleep(wait)
                        continue
                    raise last_error

                resp.raise_for_status()
                return resp.json()

            except requests.RequestException as e:
                last_error = e
                # A reset connection is Cloudflare hanging up on us; the
                # only useful response is to wait longer, not try harder.
                reset = isinstance(e, requests.ConnectionError)
                wait = (5 * (attempt + 1)) if reset else (2 ** attempt)
                logger.warning("%s %s attempt %d failed: %s",
                               method, url, attempt + 1, e)
                if attempt < retries - 1:
                    time.sleep(wait)

        if last_error:
            raise last_error
        return {}
