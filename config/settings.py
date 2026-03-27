"""Bot configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    VERSION = "2.1.0"

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
    MIN_LIQUIDITY = 200        # Minimum market liquidity in USD (lowered from 500)
    REBALANCE_INTERVAL = 300   # Seconds between strategy cycles

    # Risk
    MAX_LOSS_PER_TRADE = 0.05   # 5% of balance max loss per trade
    STOP_LOSS_PCT = 0.15        # 15% individual position stop-loss
    MAX_OPEN_POSITIONS = 10
    KELLY_FRACTION = 0.25       # Use 25% of full Kelly criterion

    # Take-profit & trailing stop
    TAKE_PROFIT_PCT = 0.40      # Take profit at +40% gain
    TRAILING_STOP_ACTIVATION = 0.15   # Activate trailing stop after +15% gain
    TRAILING_STOP_DISTANCE = 0.08     # Trail 8% below peak price

    # Slippage simulation (paper trading)
    SLIPPAGE_BPS = 50           # 50 basis points (0.5%) simulated slippage

    # Strategy thresholds (previously hardcoded)
    MISPRICING_THRESHOLD = 0.02       # Min total deviation from 1.0
    MISPRICING_MIN_EDGE = 0.02        # Min edge for mispricing signal
    MEAN_REVERSION_DEVIATION = -1.5   # Std deviations below mean
    MEAN_REVERSION_MAX_PRICE = 0.85   # Max price for mean reversion
    MOMENTUM_THRESHOLD = 0.05         # Min momentum for signal
    MOMENTUM_MAX_PRICE = 0.80         # Max price for momentum signal

    # Gemini AI daily analysis
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANALYSIS_HOUR = int(os.getenv("ANALYSIS_HOUR", "23"))  # Run at 23:00 daily
