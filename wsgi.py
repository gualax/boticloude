"""WSGI entry point for running the dashboard under gunicorn.

    gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8080 wsgi:app

The single worker is not a suggestion. Each worker is its own process
with its own bot, its own positions and its own writes to
data/paper_state.json — two of them would trade twice and corrupt each
other's state. Concurrency comes from threads, never from workers.
"""
import logging

from config import Config
from src.utils.logger import setup_logging
from src.web import app, prepare, require_password_for_production

setup_logging()
logger = logging.getLogger(__name__)

_config = Config()

# gunicorn binds the socket itself, so the host is not visible here —
# refuse outright if the panel would come up without a login.
require_password_for_production(_config)

prepare(auto_start_bot=True)
logger.info("WSGI app ready — bot v%s running in background", _config.VERSION)

__all__ = ["app"]
