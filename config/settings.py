"""Bot configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    VERSION = "2.2.0"

    # API
    CLOB_API_URL = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
    GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
    API_KEY = os.getenv("POLYMARKET_API_KEY", "")
    API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
    API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

    # Trading
    PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
    MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "50"))
    MAX_TOTAL_EXPOSURE = float(os.getenv("MAX_TOTAL_EXPOSURE", "500"))
    INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "1000"))

    # Strategy
    MIN_EDGE = 0.03            # Minimum edge (3%) to place a trade
    MIN_LIQUIDITY = 200        # Minimum market liquidity in USD
    REBALANCE_INTERVAL = 300   # Seconds between strategy cycles

    # Price boundaries — reject penny tokens and near-certain outcomes
    MIN_PRICE = 0.05           # Don't buy tokens below $0.05
    MAX_PRICE = 0.95           # Don't buy tokens above $0.95

    # Risk
    MAX_LOSS_PER_TRADE = 0.05   # 5% of balance max loss per trade
    STOP_LOSS_PCT = 0.15        # 15% individual position stop-loss
    MAX_OPEN_POSITIONS = 10
    KELLY_FRACTION = 0.25       # Use 25% of full Kelly criterion

    # Take-profit & trailing stop
    TAKE_PROFIT_PCT = 0.40      # Take profit at +40% gain
    TRAILING_STOP_ACTIVATION = 0.15   # Activate trailing stop after +15% gain
    TRAILING_STOP_DISTANCE = 0.08     # Trail 8% below peak price

    # Cooldown — avoid re-entering a market after a loss
    LOSS_COOLDOWN_CYCLES = 50   # Skip market for 50 cycles (~4h) after a loss

    # Per-market exposure limit
    MAX_EXPOSURE_PER_MARKET = 60.0  # Max USD in a single market question

    # Slippage simulation (paper trading)
    SLIPPAGE_BPS = 50           # 50 basis points (0.5%) simulated slippage

    # Strategy thresholds
    MISPRICING_THRESHOLD = 0.02       # Min total deviation from 1.0
    MISPRICING_MIN_EDGE = 0.02        # Min edge for mispricing signal
    MEAN_REVERSION_DEVIATION = -1.5   # Std deviations below mean
    MEAN_REVERSION_MAX_PRICE = 0.85   # Max price for mean reversion
    MOMENTUM_THRESHOLD = 0.05         # Min momentum for signal
    MOMENTUM_MAX_PRICE = 0.80         # Max price for momentum signal

    # Gemini AI daily analysis
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANALYSIS_HOUR = int(os.getenv("ANALYSIS_HOUR", "23"))  # Run at 23:00 daily

    # --- Safety limits for auto-tuning (Gemini can't go beyond these) ---
    TUNABLE_PARAMS = {
        "MIN_EDGE":        {"min": 0.01, "max": 0.10, "type": float},
        "MIN_LIQUIDITY":   {"min": 50,   "max": 2000, "type": float},
        "STOP_LOSS_PCT":   {"min": 0.05, "max": 0.30, "type": float},
        "TAKE_PROFIT_PCT": {"min": 0.10, "max": 0.80, "type": float},
        "KELLY_FRACTION":  {"min": 0.10, "max": 0.50, "type": float},
        "MAX_POSITION_SIZE":       {"min": 10,   "max": 100,  "type": float},
        "MAX_TOTAL_EXPOSURE":      {"min": 100,  "max": 800,  "type": float},
        "TRAILING_STOP_ACTIVATION": {"min": 0.05, "max": 0.30, "type": float},
        "TRAILING_STOP_DISTANCE":   {"min": 0.03, "max": 0.15, "type": float},
        "MISPRICING_THRESHOLD":     {"min": 0.01, "max": 0.05, "type": float},
        "MEAN_REVERSION_DEVIATION": {"min": -3.0, "max": -0.5, "type": float},
        "MOMENTUM_THRESHOLD":       {"min": 0.02, "max": 0.15, "type": float},
        "MIN_PRICE":        {"min": 0.03, "max": 0.15, "type": float},
        "MAX_PRICE":        {"min": 0.85, "max": 0.98, "type": float},
        "LOSS_COOLDOWN_CYCLES":     {"min": 10,   "max": 200,  "type": int},
        "MAX_EXPOSURE_PER_MARKET":  {"min": 20,   "max": 150,  "type": float},
    }
