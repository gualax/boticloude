"""Tests for the Bitcoin/crypto pricing model."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from config import Config
from src.analysis.crypto_strategy import (CryptoStrategy, classify_question,
                                          parse_expiry_years, parse_strike,
                                          prob_above_terminal, prob_touch)


class TestStrikeParsing:
    @pytest.mark.parametrize("question,expected", [
        ("Will Bitcoin reach $150,000 by June?", 150_000),
        ("Bitcoin above $100k on March 31?", 100_000),
        ("Will BTC hit 200K this year?", 200_000),
        ("Ethereum over $5,000?", 5_000),
        ("Will BTC reach $1.5M eventually?", 1_500_000),
    ])
    def test_parses_amounts(self, question, expected):
        assert parse_strike(question) == expected

    def test_ignores_bare_years(self):
        assert parse_strike("Will Bitcoin win in 2026?") is None

    def test_returns_none_without_amount(self):
        assert parse_strike("Will Bitcoin go up?") is None


class TestQuestionClassification:
    def test_detects_asset_and_direction(self):
        parsed = classify_question("Will Bitcoin reach $150,000 by June?")
        assert parsed["asset"] == "BTC"
        assert parsed["direction"] == "up"
        assert parsed["strike"] == 150_000
        assert parsed["barrier"] is True

    def test_detects_downside(self):
        parsed = classify_question("Will Bitcoin fall below $50,000 on June 1?")
        assert parsed["direction"] == "down"
        assert parsed["barrier"] is False

    def test_detects_ethereum(self):
        assert classify_question("Will Ethereum top $6,000 by May?")["asset"] == "ETH"

    def test_rejects_non_crypto(self):
        assert classify_question("Will Italy qualify for the World Cup?") is None

    def test_rejects_ranges(self):
        assert classify_question(
            "Will Bitcoin be between $90,000 and $110,000?") is None


class TestProbabilityModel:
    def test_terminal_at_the_money_is_near_half(self):
        # With zero drift, P(S_T > S_0) sits just under 50% because of the
        # -sigma^2/2 term in the lognormal drift.
        p = prob_above_terminal(100_000, 100_000, 0.25, 0.50)
        assert 0.40 < p < 0.50

    def test_terminal_far_out_of_the_money_is_unlikely(self):
        p = prob_above_terminal(100_000, 500_000, 0.08, 0.50)
        assert p < 0.02

    def test_terminal_deep_in_the_money_is_likely(self):
        p = prob_above_terminal(100_000, 20_000, 0.25, 0.50)
        assert p > 0.98

    def test_touch_is_always_at_least_terminal(self):
        # Touching a barrier is strictly easier than finishing beyond it.
        terminal = prob_above_terminal(100_000, 130_000, 0.5, 0.6)
        touch = prob_touch(100_000, 130_000, 0.5, 0.6)
        assert touch >= terminal

    def test_touch_at_spot_is_certain(self):
        assert prob_touch(100_000, 100_000, 0.25, 0.5) == pytest.approx(1.0, abs=1e-6)

    def test_touch_below_spot_is_likely_but_not_certain(self):
        # A level 10% under spot is very reachable in 3 months at 50% vol,
        # but never a certainty.
        p = prob_touch(100_000, 90_000, 0.25, 0.5)
        assert 0.5 < p < 1.0

    def test_already_met_barrier_resolves_yes(self):
        strategy = CryptoStrategy(FakeCryptoClient(spot=120_000), Config())
        future = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
        # Spot is already above the threshold, so the answer is settled
        model = strategy.model_probability(
            "Will Bitcoin reach $100,000 by June?", future)
        assert model["fair_probability"] == 1.0

    def test_already_met_downside_barrier_resolves_yes(self):
        strategy = CryptoStrategy(FakeCryptoClient(spot=40_000), Config())
        future = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
        model = strategy.model_probability(
            "Will Bitcoin fall below $50,000 by June?", future)
        assert model["fair_probability"] == 1.0

    def test_probabilities_stay_in_range(self):
        for strike in (10_000, 80_000, 100_000, 250_000, 900_000):
            for years in (0.02, 0.5, 2.0):
                assert 0.0 <= prob_touch(100_000, strike, years, 0.55) <= 1.0
                assert 0.0 <= prob_above_terminal(100_000, strike, years, 0.55) <= 1.0

    def test_higher_volatility_raises_touch_probability(self):
        low = prob_touch(100_000, 140_000, 0.5, 0.30)
        high = prob_touch(100_000, 140_000, 0.5, 0.90)
        assert high > low


class TestExpiryParsing:
    def test_future_date_gives_positive_years(self):
        future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        years = parse_expiry_years(future)
        assert years == pytest.approx(1.0, abs=0.02)

    def test_past_date_returns_none(self):
        past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert parse_expiry_years(past) is None

    def test_garbage_returns_none(self):
        assert parse_expiry_years("not a date") is None
        assert parse_expiry_years("") is None


class FakeCryptoClient:
    """Stand-in so the model can be tested without network access."""

    def __init__(self, spot=100_000.0, vol=0.55):
        self.spot = spot
        self.vol = vol

    def get_volatility(self, symbol="BTC", days=30):
        return {
            "symbol": symbol,
            "spot": self.spot,
            "daily_vol": self.vol / math.sqrt(365),
            "annualized_vol": self.vol,
            "change_24h": 1.2,
            "samples": 30,
        }


class TestCryptoStrategy:
    @pytest.fixture
    def strategy(self):
        return CryptoStrategy(FakeCryptoClient(), Config())

    def test_identifies_crypto_markets(self, strategy):
        assert strategy.is_crypto_market("Will Bitcoin reach $150,000 by June?")
        assert not strategy.is_crypto_market("Will Italy qualify for the World Cup?")

    def test_model_probability_is_sane(self, strategy):
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        model = strategy.model_probability(
            "Will Bitcoin reach $150,000 by June?", future)
        assert model is not None
        assert 0.0 <= model["fair_probability"] <= 1.0
        assert model["asset"] == "BTC"
        assert model["strike"] == 150_000

    def test_rejects_implausible_strike(self, strategy):
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        # 50x spot — almost certainly a parsing error, not a real market
        assert strategy.model_probability(
            "Will Bitcoin reach $5,000,000 by June?", future) is None

    def test_generates_signal_when_market_is_mispriced(self, strategy):
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        market = {
            "question": "Will Bitcoin reach $110,000 by June?",
            "condition_id": "c1",
            "end_date": future,
            "liquidity": 5000,
            "volume": 20000,
        }
        # Model says the touch is likely; market prices YES at a steep discount
        prices = {
            "Yes": {"token_id": "yes-token", "midpoint": 0.10},
            "No": {"token_id": "no-token", "midpoint": 0.90},
        }
        signals = strategy.generate_signals(market, prices)
        assert signals, "expected a signal when the model disagrees strongly"
        assert signals[0].strategy == "crypto_model"
        assert signals[0].edge >= Config.CRYPTO_MIN_EDGE

    def test_no_signal_when_market_agrees(self, strategy):
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        market = {
            "question": "Will Bitcoin reach $110,000 by June?",
            "condition_id": "c1",
            "end_date": future,
            "liquidity": 5000,
            "volume": 20000,
        }
        model = strategy.model_probability(market["question"], future)
        fair = model["fair_probability"]
        # Price both sides exactly at model fair value — no edge to take
        prices = {
            "Yes": {"token_id": "yes-token", "midpoint": round(fair, 4)},
            "No": {"token_id": "no-token", "midpoint": round(1 - fair, 4)},
        }
        assert strategy.generate_signals(market, prices) == []

    def test_ignores_non_crypto_market(self, strategy):
        assert strategy.generate_signals(
            {"question": "Will Poland qualify?", "end_date": "", "condition_id": "x"},
            {},
        ) == []
