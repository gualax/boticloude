"""Bot configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
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
    MIN_EDGE = 0.03          # Minimum edge (3%) to place a trade
    MIN_LIQUIDITY = 500      # Minimum market liquidity in USD
    REBALANCE_INTERVAL = 300  # Seconds between strategy cycles

    # Risk
    MAX_LOSS_PER_TRADE = 0.05   # 5% of balance max loss per trade
    STOP_LOSS_PCT = 0.15        # 15% portfolio stop-loss
    MAX_OPEN_POSITIONS = 10
