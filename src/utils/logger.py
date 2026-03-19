"""Logging configuration with rich output."""
import logging
import sys


def setup_logging(level: str = "INFO"):
    """Configure logging for the bot."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("data/bot.log", mode="a"),
        ],
    )

    # Silence noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
