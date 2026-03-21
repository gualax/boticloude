"""Flask web server exposing bot data as JSON + serving the dashboard UI."""
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from flask import Flask, jsonify, render_template, request

from config import Config
from src.bot import PolymarketBot

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config["JSON_SORT_KEYS"] = False

# Global bot instance — shared between the background thread and Flask routes
_bot: Optional[PolymarketBot] = None
_bot_thread: Optional[threading.Thread] = None
_bot_log: list[dict] = []          # last N log entries
_last_signals: list[dict] = []     # cached signals from last cycle
_cycle_history: list[dict] = []    # PnL snapshot per cycle

MAX_LOG_ENTRIES = 200


# ── Helpers ──────────────────────────────────────────────────────────────

def _serialize_position(pos) -> dict:
    return {
        "token_id": pos.token_id,
        "market_name": pos.market_name,
        "side": pos.side,
        "entry_price": round(pos.entry_price, 4),
        "current_price": round(pos.current_price, 4),
        "size": round(pos.size, 1),
        "cost": round(pos.cost, 2),
        "pnl": round(pos.pnl, 2),
        "pnl_pct": round(pos.pnl_pct, 1),
        "timestamp": pos.timestamp,
    }


def _serialize_trade(trade) -> dict:
    return {
        "token_id": trade.token_id,
        "market_name": trade.market_name,
        "side": trade.side,
        "action": trade.action,
        "price": round(trade.price, 4),
        "size": round(trade.size, 1),
        "cost": round(trade.cost, 2),
        "pnl": round(trade.pnl, 2),
        "timestamp": trade.timestamp,
        "time_str": datetime.fromtimestamp(trade.timestamp).strftime("%H:%M:%S"),
    }


def _serialize_signal(sig) -> dict:
    return {
        "token_id": sig.token_id,
        "market_name": sig.market_name,
        "side": sig.side,
        "current_price": round(sig.current_price, 4),
        "fair_value": round(sig.fair_value, 4),
        "edge": round(sig.edge * 100, 1),
        "confidence": round(sig.confidence * 100, 0),
        "strategy": sig.strategy,
    }


class WebLogHandler(logging.Handler):
    """Captures log records into the in-memory buffer for the web UI."""

    def emit(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "msg": self.format(record),
        }
        _bot_log.append(entry)
        if len(_bot_log) > MAX_LOG_ENTRIES:
            _bot_log.pop(0)


# ── Background bot runner ────────────────────────────────────────────────

def _run_bot_loop():
    """Run the bot in a background thread, recording data each cycle."""
    global _last_signals
    while _bot and _bot.is_running:
        try:
            # Run one cycle
            result = _bot.run_cycle()

            # Cache signals
            try:
                markets = _bot.analyzer.scan_markets(limit=50)
                signals = _bot.analyzer.generate_signals(markets)
                _last_signals = [_serialize_signal(s) for s in signals[:20]]
            except Exception:
                pass

            # Record PnL snapshot
            summary = _bot.paper.get_summary()
            _cycle_history.append({
                "cycle": _bot.cycle_count,
                "ts": time.time(),
                "time_str": datetime.now().strftime("%H:%M:%S"),
                "portfolio_value": summary["portfolio_value"],
                "balance": summary["balance"],
                "unrealized_pnl": summary["unrealized_pnl"],
                "realized_pnl": summary["realized_pnl"],
                "return_pct": summary["total_return_pct"],
                "trades": result.get("trades_executed", 0),
                "signals": result.get("signals_found", 0),
            })
            # Keep last 500 snapshots
            if len(_cycle_history) > 500:
                _cycle_history.pop(0)

        except Exception as e:
            logger.error("Bot cycle error: %s", e)

        # Wait for next cycle
        time.sleep(_bot.config.REBALANCE_INTERVAL)


# ── Routes: Pages ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


# ── Routes: API ──────────────────────────────────────────────────────────

@app.route("/api/summary")
def api_summary():
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    summary = _bot.paper.get_summary()
    summary["cycle"] = _bot.cycle_count
    summary["is_running"] = _bot.is_running
    summary["mode"] = "PAPER" if _bot.config.PAPER_TRADING else "LIVE"
    summary["initial_balance"] = _bot.paper.initial_balance
    summary["version"] = _bot.config.VERSION
    return jsonify(summary)


@app.route("/api/positions")
def api_positions():
    if not _bot:
        return jsonify([])
    positions = [_serialize_position(p) for p in _bot.paper.positions.values()]
    return jsonify(positions)


@app.route("/api/trades")
def api_trades():
    if not _bot:
        return jsonify([])
    limit = request.args.get("limit", 50, type=int)
    trades = [_serialize_trade(t) for t in reversed(_bot.paper.trade_history[-limit:])]
    return jsonify(trades)


@app.route("/api/signals")
def api_signals():
    return jsonify(_last_signals)


@app.route("/api/history")
def api_history():
    return jsonify(_cycle_history)


@app.route("/api/logs")
def api_logs():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(_bot_log[-limit:])


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    global _bot, _bot_thread
    if _bot and _bot.is_running:
        return jsonify({"status": "already_running"})
    _bot = PolymarketBot(Config())
    _bot.is_running = True
    _bot_thread = threading.Thread(target=_run_bot_loop, daemon=True)
    _bot_thread.start()
    return jsonify({"status": "started"})


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    if _bot:
        _bot.is_running = False
    return jsonify({"status": "stopped"})


# ── Entry point ──────────────────────────────────────────────────────────

def start_web(host: str = "0.0.0.0", port: int = 8080, auto_start_bot: bool = True):
    """Start the web dashboard and optionally launch the bot."""
    global _bot, _bot_thread

    # Attach web log handler
    web_handler = WebLogHandler()
    web_handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
    logging.getLogger().addHandler(web_handler)

    if auto_start_bot:
        _bot = PolymarketBot(Config())
        _bot.is_running = True
        _bot_thread = threading.Thread(target=_run_bot_loop, daemon=True)
        _bot_thread.start()
        logger.info("Bot started in background thread")

    logger.info("Dashboard available at http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
