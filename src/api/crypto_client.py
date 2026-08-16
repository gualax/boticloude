"""Crypto market data client — real BTC/ETH spot prices and volatility.

Uses Binance public API (no key required) with CoinGecko as fallback.
This data feeds the crypto pricing model that values Polymarket
Bitcoin threshold markets.
"""
import logging
import math
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BINANCE_URL = "https://api.binance.com/api/v3"
COINGECKO_URL = "https://api.coingecko.com/api/v3"

# Polymarket crypto markets we care about
SYMBOLS = {
    "BTC": {"binance": "BTCUSDT", "coingecko": "bitcoin"},
    "ETH": {"binance": "ETHUSDT", "coingecko": "ethereum"},
    "SOL": {"binance": "SOLUSDT", "coingecko": "solana"},
}


class CryptoClient:
    """Fetches spot prices and realized volatility for crypto assets."""

    def __init__(self, cache_ttl: int = 60):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.cache_ttl = cache_ttl
        self._price_cache: dict[str, tuple[float, float]] = {}   # sym -> (price, ts)
        self._vol_cache: dict[str, tuple[dict, float]] = {}      # sym -> (stats, ts)

    # ── Spot price ──────────────────────────────────────────────────────
    def get_spot(self, symbol: str = "BTC") -> Optional[float]:
        """Get current spot price in USD."""
        symbol = symbol.upper()
        cached = self._price_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self.cache_ttl:
            return cached[0]

        price = self._binance_spot(symbol) or self._coingecko_spot(symbol)
        if price is not None:
            self._price_cache[symbol] = (price, time.time())
        return price

    def _binance_spot(self, symbol: str) -> Optional[float]:
        pair = SYMBOLS.get(symbol, {}).get("binance")
        if not pair:
            return None
        try:
            r = self.session.get(f"{BINANCE_URL}/ticker/price",
                                 params={"symbol": pair}, timeout=10)
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception as e:
            logger.debug("Binance spot failed for %s: %s", symbol, e)
            return None

    def _coingecko_spot(self, symbol: str) -> Optional[float]:
        coin = SYMBOLS.get(symbol, {}).get("coingecko")
        if not coin:
            return None
        try:
            r = self.session.get(f"{COINGECKO_URL}/simple/price",
                                 params={"ids": coin, "vs_currencies": "usd"},
                                 timeout=10)
            r.raise_for_status()
            return float(r.json()[coin]["usd"])
        except Exception as e:
            logger.debug("CoinGecko spot failed for %s: %s", symbol, e)
            return None

    # ── Volatility ──────────────────────────────────────────────────────
    def get_volatility(self, symbol: str = "BTC", days: int = 30) -> Optional[dict]:
        """Compute annualized realized volatility from daily closes.

        Returns dict with annualized_vol, daily_vol, spot, and 24h change.
        """
        symbol = symbol.upper()
        cached = self._vol_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self.cache_ttl * 10:
            return cached[0]

        closes = self._binance_klines(symbol, days + 1)
        if not closes or len(closes) < 8:
            return None

        # Daily log returns
        returns = [
            math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        if len(returns) < 7:
            return None

        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        annualized = daily_vol * math.sqrt(365)

        change_24h = 0.0
        if len(closes) >= 2 and closes[-2] > 0:
            change_24h = (closes[-1] - closes[-2]) / closes[-2] * 100

        stats = {
            "symbol": symbol,
            "spot": closes[-1],
            "daily_vol": daily_vol,
            "annualized_vol": annualized,
            "change_24h": change_24h,
            "samples": len(returns),
        }
        self._vol_cache[symbol] = (stats, time.time())
        return stats

    def _binance_klines(self, symbol: str, limit: int) -> list[float]:
        """Fetch daily closing prices."""
        pair = SYMBOLS.get(symbol, {}).get("binance")
        if not pair:
            return []
        try:
            r = self.session.get(
                f"{BINANCE_URL}/klines",
                params={"symbol": pair, "interval": "1d", "limit": min(limit, 200)},
                timeout=12,
            )
            r.raise_for_status()
            # kline[4] is the close price
            return [float(k[4]) for k in r.json()]
        except Exception as e:
            logger.debug("Binance klines failed for %s: %s", symbol, e)
            return []

    # ── Combined snapshot for the dashboard ─────────────────────────────
    def get_snapshot(self) -> dict:
        """Get a full snapshot of all tracked crypto assets."""
        snapshot = {}
        for symbol in SYMBOLS:
            stats = self.get_volatility(symbol)
            if stats:
                snapshot[symbol] = {
                    "spot": round(stats["spot"], 2),
                    "change_24h": round(stats["change_24h"], 2),
                    "annualized_vol": round(stats["annualized_vol"] * 100, 1),
                }
            else:
                spot = self.get_spot(symbol)
                if spot:
                    snapshot[symbol] = {
                        "spot": round(spot, 2),
                        "change_24h": 0.0,
                        "annualized_vol": None,
                    }
        return snapshot
