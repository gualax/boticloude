"""Analyzes Polymarket data to find trading opportunities."""
import json
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from config import Config
from src.api.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)


@dataclass
class MarketSignal:
    """Represents a trading signal for a specific market."""
    token_id: str
    market_name: str
    condition_id: str
    side: str             # "YES" or "NO"
    current_price: float
    fair_value: float     # Our estimated fair probability
    edge: float           # fair_value - current_price
    confidence: float     # 0-1 confidence in the signal
    strategy: str         # Which strategy generated this signal
    volume_24h: float = 0.0
    liquidity: float = 0.0

    @property
    def expected_value(self) -> float:
        """Expected profit per dollar risked."""
        if self.current_price == 0:
            return 0
        return (self.fair_value / self.current_price) - 1


class MarketAnalyzer:
    """Scans markets and generates trading signals."""

    def __init__(self, client: PolymarketClient, config: Optional[Config] = None,
                 crypto_strategy=None):
        self.client = client
        self.config = config or Config()
        # Optional: prices Bitcoin/crypto threshold markets from real spot data
        self.crypto_strategy = crypto_strategy

    @staticmethod
    def _parse_json_field(value, default=None):
        """Parse a field that may be a JSON string or already a list."""
        if default is None:
            default = []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return default

    def scan_markets(self, limit: int = 100) -> list[dict]:
        """Fetch and filter active markets suitable for trading."""
        markets = self.client.get_markets(limit=limit, active=True)
        suitable = []
        for m in markets:
            # Filter for markets with sufficient liquidity
            try:
                volume = float(m.get("volume", 0) or 0)
                liquidity = float(m.get("liquidity", 0) or 0)
            except (ValueError, TypeError):
                continue

            if liquidity < 100:
                continue

            # Gamma API returns clobTokenIds and outcomes as JSON strings
            tokens = self._parse_json_field(
                m.get("clobTokenIds", m.get("clob_token_ids", []))
            )
            if not tokens or len(tokens) < 2:
                continue

            outcomes = self._parse_json_field(
                m.get("outcomes", ""), ["Yes", "No"]
            )

            suitable.append({
                "condition_id": m.get("conditionId", m.get("condition_id", "")),
                "question": m.get("question", "Unknown"),
                "tokens": tokens,
                "outcomes": outcomes,
                "volume": volume,
                "liquidity": liquidity,
                "end_date": m.get("endDate", m.get("end_date_iso", "")),
            })

        logger.info("Found %d suitable markets out of %d total",
                     len(suitable), len(markets))
        return suitable

    def get_market_prices(self, market: dict) -> dict:
        """Get current prices for all outcomes in a market."""
        prices = {}
        tokens = market.get("tokens", [])
        outcomes = market.get("outcomes", ["Yes", "No"])

        for i, token_id in enumerate(tokens):
            mid = self.client.get_midpoint(token_id)
            if mid is not None:
                outcome_name = outcomes[i] if i < len(outcomes) else f"Outcome_{i}"
                prices[outcome_name] = {
                    "token_id": token_id,
                    "midpoint": mid,
                }

        return prices

    def analyze_price_history(self, token_id: str) -> dict:
        """Analyze price history to detect trends and volatility."""
        history = self.client.get_prices_history(token_id, interval="1w",
                                                  fidelity=60)
        if len(history) < 5:
            return {"trend": 0, "volatility": 0, "momentum": 0,
                    "mean_price": 0, "data_points": len(history)}

        prices = [float(h.get("p", h.get("price", 0))) for h in history]
        prices_arr = np.array(prices)

        mean_price = float(np.mean(prices_arr))
        volatility = float(np.std(prices_arr))

        # Simple momentum: compare recent vs older prices
        recent = prices_arr[-5:]
        older = prices_arr[-10:-5] if len(prices_arr) >= 10 else prices_arr[:5]
        momentum = float(np.mean(recent) - np.mean(older))

        # Trend: linear regression slope
        x = np.arange(len(prices_arr))
        if len(x) > 1:
            slope = float(np.polyfit(x, prices_arr, 1)[0])
        else:
            slope = 0

        return {
            "trend": slope,
            "volatility": volatility,
            "momentum": momentum,
            "mean_price": mean_price,
            "current_price": prices[-1] if prices else 0,
            "data_points": len(prices),
        }

    def detect_mispricing(self, market: dict,
                          prices: Optional[dict] = None) -> list[MarketSignal]:
        """Detect mispricings in a binary market (YES/NO should sum to ~1.0)."""
        signals = []
        if prices is None:
            prices = self.get_market_prices(market)

        if len(prices) < 2:
            return signals

        outcomes = list(prices.values())
        yes_data = outcomes[0]
        no_data = outcomes[1]

        yes_price = yes_data["midpoint"]
        no_price = no_data["midpoint"]
        total = yes_price + no_price

        threshold = self.config.MISPRICING_THRESHOLD
        min_edge = self.config.MISPRICING_MIN_EDGE

        # If prices don't sum to ~1.0, there's an arbitrage opportunity
        if total > 0 and abs(total - 1.0) > threshold:
            # Overpriced total → sell the overpriced side
            if total > 1.0 + threshold:
                # Both sides overpriced, but relatively the higher one more so
                if yes_price > no_price:
                    fair = 1.0 - no_price
                    edge = yes_price - fair
                    if edge > min_edge:
                        signals.append(MarketSignal(
                            token_id=no_data["token_id"],
                            market_name=market["question"],
                            condition_id=market["condition_id"],
                            side="NO",
                            current_price=no_price,
                            fair_value=1.0 - yes_price,
                            edge=edge,
                            confidence=min(edge * 5, 0.9),
                            strategy="mispricing",
                            volume_24h=market.get("volume", 0),
                            liquidity=market.get("liquidity", 0),
                        ))
            elif total < 1.0 - threshold:
                # Underpriced — buy the cheaper side
                cheaper = "YES" if yes_price < no_price else "NO"
                data = yes_data if cheaper == "YES" else no_data
                price = yes_price if cheaper == "YES" else no_price
                fair = 1.0 - (no_price if cheaper == "YES" else yes_price)
                edge = fair - price

                if edge > min_edge:
                    signals.append(MarketSignal(
                        token_id=data["token_id"],
                        market_name=market["question"],
                        condition_id=market["condition_id"],
                        side=cheaper,
                        current_price=price,
                        fair_value=fair,
                        edge=edge,
                        confidence=min(edge * 5, 0.9),
                        strategy="mispricing",
                        volume_24h=market.get("volume", 0),
                        liquidity=market.get("liquidity", 0),
                    ))

        return signals

    def generate_signals(self, markets: Optional[list[dict]] = None) -> list[MarketSignal]:
        """Generate trading signals across all suitable markets."""
        if markets is None:
            markets = self.scan_markets()

        all_signals = []

        for market in markets:
            # Fetch live prices once and share them across strategies
            prices = self.get_market_prices(market)

            # 1. Check for mispricings
            all_signals.extend(self.detect_mispricing(market, prices))

            # 1b. Crypto markets get priced from real spot + volatility.
            #     This is an independent edge that price history cannot see,
            #     so we skip the trend strategies for these markets.
            if (self.crypto_strategy
                    and self.crypto_strategy.is_crypto_market(
                        market.get("question", ""))):
                crypto_signals = self.crypto_strategy.generate_signals(
                    market, prices)
                all_signals.extend(crypto_signals)
                if crypto_signals:
                    continue

            # 2. Analyze price trends
            for i, token_id in enumerate(market.get("tokens", [])):
                analysis = self.analyze_price_history(token_id)
                if analysis["data_points"] < 5:
                    continue

                current = analysis["current_price"]
                mean = analysis["mean_price"]
                volatility = analysis["volatility"]
                momentum = analysis["momentum"]

                if current == 0 or mean == 0:
                    continue

                outcome = market["outcomes"][i] if i < len(market["outcomes"]) else "YES"

                # Mean reversion: if price deviates significantly from mean
                deviation = (current - mean) / max(volatility, 0.01)
                if (deviation < self.config.MEAN_REVERSION_DEVIATION
                        and current < self.config.MEAN_REVERSION_MAX_PRICE):
                    edge = mean - current
                    if edge > self.config.MIN_EDGE:
                        all_signals.append(MarketSignal(
                            token_id=token_id,
                            market_name=market["question"],
                            condition_id=market["condition_id"],
                            side=outcome,
                            current_price=current,
                            fair_value=mean,
                            edge=edge,
                            confidence=min(abs(deviation) * 0.2, 0.8),
                            strategy="mean_reversion",
                            volume_24h=market.get("volume", 0),
                            liquidity=market.get("liquidity", 0),
                        ))

                # Momentum: strong positive trend
                if (momentum > self.config.MOMENTUM_THRESHOLD
                        and analysis["trend"] > 0
                        and current < self.config.MOMENTUM_MAX_PRICE):
                    fair_value = min(current + momentum * 0.5, 0.95)
                    edge = fair_value - current
                    if edge > self.config.MIN_EDGE:
                        all_signals.append(MarketSignal(
                            token_id=token_id,
                            market_name=market["question"],
                            condition_id=market["condition_id"],
                            side=outcome,
                            current_price=current,
                            fair_value=fair_value,
                            edge=edge,
                            confidence=min(momentum * 3, 0.7),
                            strategy="momentum",
                            volume_24h=market.get("volume", 0),
                            liquidity=market.get("liquidity", 0),
                        ))

        # Sort by edge * confidence (expected value)
        all_signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
        logger.info("Generated %d trading signals", len(all_signals))
        return all_signals
