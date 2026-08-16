"""Crypto threshold-market pricing model.

Polymarket lists markets like "Will Bitcoin reach $150,000 by June 30?".
Given the real BTC spot price and realized volatility, we can compute a
model fair probability and compare it to the market price. The gap is a
genuine, independent edge that the price-history strategies cannot see.

Two market shapes are handled:
  - Terminal  ("on <date>", "at <date>")  -> P(S_T > K)
  - Barrier   ("by <date>", "before ...") -> P(max S_t > K over [0,T])
"""
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional

from src.analysis.market_analyzer import MarketSignal

logger = logging.getLogger(__name__)

# Which asset a question refers to
ASSET_PATTERNS = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.I),
    "ETH": re.compile(r"\b(ethereum|ether|eth)\b", re.I),
    "SOL": re.compile(r"\b(solana|sol)\b", re.I),
}

# Direction of the threshold
UP_WORDS = re.compile(
    r"\b(above|over|reach|reaches|hit|hits|exceed|exceeds|surpass|surpasses|"
    r"greater|higher|up to|top)\b", re.I)
DOWN_WORDS = re.compile(
    r"\b(below|under|dip|dips|drop|drops|fall|falls|less than|lower|down to)\b",
    re.I)

# Barrier vs terminal wording
BARRIER_WORDS = re.compile(r"\b(by|before|any ?time|ever|during)\b", re.I)

# Dollar amount: $150,000 / $150k / 150K / $1.5M
PRICE_PATTERN = re.compile(
    r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.[0-9]+)?)\s*([kKmM])?\b")

# Ranges are ambiguous for a single-threshold model — skip them
RANGE_WORDS = re.compile(r"\bbetween\b|\brange\b", re.I)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_above_terminal(spot: float, strike: float, years: float,
                        sigma: float) -> float:
    """P(S_T > K) under driftless lognormal dynamics (the N(d2) term)."""
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = (math.log(spot / strike) - 0.5 * sigma ** 2 * years) / (sigma * math.sqrt(years))
    return _norm_cdf(d2)


def prob_touch(spot: float, strike: float, years: float, sigma: float) -> float:
    """P(the barrier at `strike` is touched at any point before T).

    First-passage probability for Brownian motion with drift mu = -sigma^2/2
    on the log-price. Exact, not an approximation.
    """
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    # Already through the barrier
    b = math.log(strike / spot)
    mu = -0.5 * sigma ** 2
    s = sigma * math.sqrt(years)

    # Sitting exactly on the barrier means it is touched immediately
    if abs(b) < 1e-12:
        return 1.0

    if b > 0:  # barrier above spot
        term1 = _norm_cdf((mu * years - b) / s)
        term2 = math.exp(-b) * _norm_cdf((-b - mu * years) / s)
    else:      # barrier below spot
        term1 = _norm_cdf((b - mu * years) / s)
        term2 = math.exp(-b) * _norm_cdf((b + mu * years) / s)

    return max(0.0, min(1.0, term1 + term2))


def parse_strike(question: str) -> Optional[float]:
    """Extract the dollar threshold from a market question."""
    for match in PRICE_PATTERN.finditer(question):
        raw, suffix = match.group(1), match.group(2)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix and suffix.lower() == "k":
            value *= 1_000
        elif suffix and suffix.lower() == "m":
            value *= 1_000_000
        # Ignore years and other small numbers that aren't prices
        if value < 100:
            continue
        if 1900 <= value <= 2100 and not suffix and "$" not in match.group(0):
            continue
        return value
    return None


def parse_expiry_years(end_date: str) -> Optional[float]:
    """Convert an ISO end date to years from now."""
    if not end_date:
        return None
    text = end_date.strip().replace("Z", "+00:00")
    for parser in (
        lambda s: datetime.fromisoformat(s),
        lambda s: datetime.strptime(s, "%Y-%m-%d"),
    ):
        try:
            dt = parser(text)
            break
        except (ValueError, TypeError):
            continue
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (dt - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return None
    return seconds / (365.25 * 24 * 3600)


def classify_question(question: str) -> Optional[dict]:
    """Work out the asset, direction and shape of a crypto market question."""
    if RANGE_WORDS.search(question):
        return None

    asset = next((a for a, pat in ASSET_PATTERNS.items()
                  if pat.search(question)), None)
    if not asset:
        return None

    strike = parse_strike(question)
    if strike is None:
        return None

    down = bool(DOWN_WORDS.search(question))
    up = bool(UP_WORDS.search(question))
    if down and not up:
        direction = "down"
    elif up and not down:
        direction = "up"
    else:
        # Default: a threshold market with no clear verb is "above"
        direction = "up"

    return {
        "asset": asset,
        "strike": strike,
        "direction": direction,
        "barrier": bool(BARRIER_WORDS.search(question)),
    }


class CryptoStrategy:
    """Prices Polymarket crypto threshold markets from real spot + volatility."""

    def __init__(self, crypto_client, config):
        self.crypto = crypto_client
        self.config = config

    def is_crypto_market(self, question: str) -> bool:
        return classify_question(question) is not None

    def model_probability(self, question: str, end_date: str) -> Optional[dict]:
        """Return the model's fair probability for the YES outcome."""
        parsed = classify_question(question)
        if not parsed:
            return None

        years = parse_expiry_years(end_date)
        if years is None or years > 3.0:
            return None

        stats = self.crypto.get_volatility(parsed["asset"])
        if not stats or not stats.get("annualized_vol"):
            return None

        spot = stats["spot"]
        sigma = stats["annualized_vol"]
        strike = parsed["strike"]

        # Guard against a badly parsed strike (e.g. off by 1000x)
        ratio = strike / spot if spot else 0
        if ratio <= 0 or ratio > 20 or ratio < 0.05:
            logger.debug("Rejecting implausible strike %.0f vs spot %.0f",
                         strike, spot)
            return None

        if parsed["barrier"]:
            # A barrier question is already resolved YES if spot is past the
            # threshold in the direction being asked about.
            already_met = (
                (parsed["direction"] == "up" and spot >= strike)
                or (parsed["direction"] == "down" and spot <= strike)
            )
            prob = 1.0 if already_met else prob_touch(spot, strike, years, sigma)
        else:
            prob_up = prob_above_terminal(spot, strike, years, sigma)
            prob = prob_up if parsed["direction"] == "up" else 1.0 - prob_up

        return {
            "fair_probability": prob,
            "asset": parsed["asset"],
            "strike": strike,
            "spot": spot,
            "sigma": sigma,
            "years": years,
            "direction": parsed["direction"],
            "barrier": parsed["barrier"],
        }

    def generate_signals(self, market: dict, prices: dict) -> list[MarketSignal]:
        """Compare the model probability against live market prices."""
        question = market.get("question", "")
        model = self.model_probability(question, market.get("end_date", ""))
        if not model:
            return []

        fair_yes = model["fair_probability"]
        outcomes = list(prices.items())
        if len(outcomes) < 2:
            return []

        signals = []
        for index, (name, data) in enumerate(outcomes[:2]):
            # First listed outcome is YES, second is NO
            fair = fair_yes if index == 0 else 1.0 - fair_yes
            price = data["midpoint"]
            if price <= 0 or price >= 1:
                continue

            edge = fair - price
            if edge < self.config.CRYPTO_MIN_EDGE:
                continue

            # Shorter horizons and calmer vol make the model more trustworthy
            horizon_factor = max(0.3, min(1.0, 0.5 / max(model["years"], 0.02)))
            confidence = min(0.85, edge * 3.0 * horizon_factor)
            if confidence < 0.15:
                continue

            signals.append(MarketSignal(
                token_id=data["token_id"],
                market_name=question,
                condition_id=market.get("condition_id", ""),
                side=name,
                current_price=price,
                fair_value=fair,
                edge=edge,
                confidence=confidence,
                strategy="crypto_model",
                volume_24h=market.get("volume", 0),
                liquidity=market.get("liquidity", 0),
            ))

        if signals:
            logger.info(
                "Crypto model: %s | %s $%.0f vs spot $%.0f | vol %.0f%% | "
                "fair YES %.3f",
                question[:50], model["direction"], model["strike"],
                model["spot"], model["sigma"] * 100, fair_yes,
            )
        return signals
