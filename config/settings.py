"""Bot configuration loaded from environment variables."""
import os
import secrets

from dotenv import load_dotenv

load_dotenv()


class Config:
    VERSION = "3.0.0"

    # ── API ─────────────────────────────────────────────────────────────
    CLOB_API_URL = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
    GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
    API_KEY = os.getenv("POLYMARKET_API_KEY", "")
    API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
    API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

    # ── Trading ─────────────────────────────────────────────────────────
    PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
    MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "50"))
    MAX_TOTAL_EXPOSURE = float(os.getenv("MAX_TOTAL_EXPOSURE", "500"))
    INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "1000"))

    # ── Strategy ────────────────────────────────────────────────────────
    MIN_EDGE = 0.03            # Minimum edge (3%) to place a trade
    MIN_LIQUIDITY = 200        # Minimum market liquidity in USD
    REBALANCE_INTERVAL = 300   # Seconds between strategy cycles

    # Price boundaries — reject penny tokens and near-certain outcomes
    MIN_PRICE = 0.05
    MAX_PRICE = 0.95

    # ── Risk ────────────────────────────────────────────────────────────
    MAX_LOSS_PER_TRADE = 0.05
    STOP_LOSS_PCT = 0.15
    MAX_OPEN_POSITIONS = 10
    KELLY_FRACTION = 0.25

    TAKE_PROFIT_PCT = 0.40
    TRAILING_STOP_ACTIVATION = 0.15
    TRAILING_STOP_DISTANCE = 0.08

    LOSS_COOLDOWN_CYCLES = 50
    MAX_EXPOSURE_PER_MARKET = 60.0

    SLIPPAGE_BPS = 50

    # ── Strategy thresholds ─────────────────────────────────────────────
    MISPRICING_THRESHOLD = 0.02
    MISPRICING_MIN_EDGE = 0.02
    MEAN_REVERSION_DEVIATION = -1.5
    MEAN_REVERSION_MAX_PRICE = 0.85
    MOMENTUM_THRESHOLD = 0.05
    MOMENTUM_MAX_PRICE = 0.80

    # ── Crypto / Bitcoin module ─────────────────────────────────────────
    CRYPTO_ENABLED = os.getenv("CRYPTO_ENABLED", "true").lower() == "true"
    CRYPTO_MIN_EDGE = 0.05      # Model must beat the market by 5pp to act
    CRYPTO_VOL_WINDOW = 30      # Days of history for realized volatility

    # ── Gemini (shared brain) ───────────────────────────────────────────
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANALYSIS_HOUR = int(os.getenv("ANALYSIS_HOUR", "23"))

    # ── Hermes learning agent ───────────────────────────────────────────
    HERMES_ENABLED = os.getenv("HERMES_ENABLED", "true").lower() == "true"
    HERMES_MODEL = os.getenv("HERMES_MODEL", "gemini-2.5-pro")
    HERMES_REFLECT_EVERY_N_TRADES = int(
        os.getenv("HERMES_REFLECT_EVERY_N_TRADES", "10"))
    HERMES_MIN_SAMPLE = 15            # Trades before stats can veto a strategy
    HERMES_VETO_WIN_RATE = 0.30       # Below this win rate, stop the strategy
    HERMES_MIN_LESSON_CONFIDENCE = 0.4
    HERMES_MIN_EVIDENCE = 3           # Trades required to accept a new lesson

    # ── Dashboard access ────────────────────────────────────────────────
    DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
    DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "") or secrets.token_hex(32)

    # ── Safety limits for auto-tuning ───────────────────────────────────
    # Gemini may adjust these, but never outside the stated bounds.
    TUNABLE_PARAMS = {
        "MIN_EDGE":                 {"min": 0.01, "max": 0.10, "type": float},
        "MIN_LIQUIDITY":            {"min": 50,   "max": 2000, "type": float},
        "STOP_LOSS_PCT":            {"min": 0.05, "max": 0.30, "type": float},
        "TAKE_PROFIT_PCT":          {"min": 0.10, "max": 0.80, "type": float},
        "KELLY_FRACTION":           {"min": 0.10, "max": 0.50, "type": float},
        "MAX_POSITION_SIZE":        {"min": 10,   "max": 100,  "type": float},
        "MAX_TOTAL_EXPOSURE":       {"min": 100,  "max": 800,  "type": float},
        "TRAILING_STOP_ACTIVATION": {"min": 0.05, "max": 0.30, "type": float},
        "TRAILING_STOP_DISTANCE":   {"min": 0.03, "max": 0.15, "type": float},
        "MISPRICING_THRESHOLD":     {"min": 0.01, "max": 0.05, "type": float},
        "MEAN_REVERSION_DEVIATION": {"min": -3.0, "max": -0.5, "type": float},
        "MOMENTUM_THRESHOLD":       {"min": 0.02, "max": 0.15, "type": float},
        "MIN_PRICE":                {"min": 0.03, "max": 0.15, "type": float},
        "MAX_PRICE":                {"min": 0.85, "max": 0.98, "type": float},
        "LOSS_COOLDOWN_CYCLES":     {"min": 10,   "max": 200,  "type": int},
        "MAX_EXPOSURE_PER_MARKET":  {"min": 20,   "max": 150,  "type": float},
        "CRYPTO_MIN_EDGE":          {"min": 0.02, "max": 0.20, "type": float},
    }
