"""Flask web server exposing bot data as JSON + serving the dashboard UI."""
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from flask import (Flask, jsonify, redirect, render_template, request,
                   session)

from config import Config
from src.auth import (auth_enabled, check_credentials, is_locked_out,
                      login_required, record_failure, record_success)
from src.bot import PolymarketBot

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config["JSON_SORT_KEYS"] = False

_config = Config()
app.secret_key = _config.SECRET_KEY
app.permanent_session_lifetime = 60 * 60 * 12  # 12 hours


def get_config() -> Config:
    return _config


guard = login_required(get_config)

# Global bot instance — shared between the background thread and Flask routes
_bot: Optional[PolymarketBot] = None
_bot_thread: Optional[threading.Thread] = None
_bot_log: list[dict] = []
_last_signals: list[dict] = []
_cycle_history: list[dict] = []
_crypto_snapshot: dict = {}

MAX_LOG_ENTRIES = 200


# ── Serializers ──────────────────────────────────────────────────────────

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
        _bot_log.append({
            "ts": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "msg": self.format(record),
        })
        if len(_bot_log) > MAX_LOG_ENTRIES:
            _bot_log.pop(0)


# ── Background bot runner ────────────────────────────────────────────────

def _run_bot_loop():
    """Run the bot in a background thread, recording data each cycle."""
    global _last_signals, _crypto_snapshot
    while _bot and _bot.is_running:
        try:
            result = _bot.run_cycle()

            try:
                markets = _bot.analyzer.scan_markets(limit=50)
                signals = _bot.analyzer.generate_signals(markets)
                _last_signals = [_serialize_signal(s) for s in signals[:20]]
            except Exception:
                pass

            if _bot.crypto:
                try:
                    _crypto_snapshot = _bot.crypto.get_snapshot()
                except Exception:
                    pass

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
            if len(_cycle_history) > 500:
                _cycle_history.pop(0)

        except Exception as e:
            logger.error("Bot cycle error: %s", e)

        time.sleep(_bot.config.REBALANCE_INTERVAL)


def _start_bot() -> bool:
    """Spin up the bot in a background thread. Returns False if already running."""
    global _bot, _bot_thread
    if _bot and _bot.is_running:
        return False
    _bot = PolymarketBot(get_config())
    _bot.is_running = True
    _bot_thread = threading.Thread(target=_run_bot_loop, daemon=True)
    _bot_thread.start()
    return True


# ── Auth routes ──────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    config = get_config()
    if not auth_enabled(config):
        return redirect("/")

    if request.method == "GET":
        return render_template("login.html", error=None)

    locked, remaining = is_locked_out()
    if locked:
        return render_template(
            "login.html",
            error=f"Demasiados intentos. Espera {remaining // 60 + 1} min.",
        ), 429

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if check_credentials(config, username, password):
        record_success()
        session.permanent = True
        session["authenticated"] = True
        session["user"] = username
        logger.info("Dashboard login succeeded for '%s'", username)
        return redirect("/")

    record_failure()
    logger.warning("Failed dashboard login attempt for '%s'", username)
    return render_template("login.html", error="Usuario o contrasena incorrectos"), 401


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── Pages ────────────────────────────────────────────────────────────────

@app.route("/")
@guard
def index():
    return render_template("dashboard.html")


# ── Core API ─────────────────────────────────────────────────────────────

@app.route("/api/summary")
@guard
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
@guard
def api_positions():
    if not _bot:
        return jsonify([])
    return jsonify([_serialize_position(p)
                    for p in _bot.paper.positions.values()])


@app.route("/api/trades")
@guard
def api_trades():
    if not _bot:
        return jsonify([])
    limit = request.args.get("limit", 50, type=int)
    return jsonify([_serialize_trade(t)
                    for t in reversed(_bot.paper.trade_history[-limit:])])


@app.route("/api/signals")
@guard
def api_signals():
    return jsonify(_last_signals)


@app.route("/api/history")
@guard
def api_history():
    return jsonify(_cycle_history)


@app.route("/api/logs")
@guard
def api_logs():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(_bot_log[-limit:])


@app.route("/api/bot/start", methods=["POST"])
@guard
def api_bot_start():
    started = _start_bot()
    return jsonify({"status": "started" if started else "already_running"})


@app.route("/api/bot/stop", methods=["POST"])
@guard
def api_bot_stop():
    if _bot:
        _bot.is_running = False
    return jsonify({"status": "stopped"})


# ── Crypto / Bitcoin ─────────────────────────────────────────────────────

@app.route("/api/crypto")
@guard
def api_crypto():
    """Live spot prices and realized volatility for tracked assets."""
    global _crypto_snapshot
    if not _bot or not _bot.crypto:
        return jsonify({"enabled": False, "assets": {}})
    if not _crypto_snapshot:
        try:
            _crypto_snapshot = _bot.crypto.get_snapshot()
        except Exception as e:
            return jsonify({"enabled": True, "assets": {}, "error": str(e)})
    return jsonify({"enabled": True, "assets": _crypto_snapshot})


# ── Hermes ───────────────────────────────────────────────────────────────

@app.route("/api/hermes")
@guard
def api_hermes():
    """Everything Hermes knows: lessons, stats and recent rulings."""
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    return jsonify(_bot.hermes.get_state())


@app.route("/api/hermes/reflect", methods=["POST"])
@guard
def api_hermes_reflect():
    """Force a reflection cycle instead of waiting for the trade threshold."""
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    if not _bot.config.GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY no configurada"}), 400
    if not _bot.hermes.memory.trade_records:
        return jsonify({"error": "Hermes aun no tiene trades cerrados"}), 400
    try:
        outcome = _bot.hermes.reflect()
        if outcome:
            return jsonify({"status": "ok", **{
                k: v for k, v in outcome.items() if k != "raw"}})
        return jsonify({"error": "La reflexion no produjo resultado"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Daily Analysis ───────────────────────────────────────────────────────

@app.route("/api/analysis/latest")
@guard
def api_analysis_latest():
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    report = _bot.daily_analyzer.get_latest_report()
    if not report:
        return jsonify({"error": "No analysis yet"}), 404
    return jsonify(report)


@app.route("/api/analysis/history")
@guard
def api_analysis_history():
    if not _bot:
        return jsonify([])
    return jsonify(_bot.daily_analyzer.get_all_reports())


@app.route("/api/analysis/run", methods=["POST"])
@guard
def api_analysis_run():
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    if not _bot.config.GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY no configurada"}), 400
    try:
        report = _bot.daily_analyzer.run_analysis(_bot)
        if report:
            return jsonify({"status": "ok", "date": report["date"]})
        return jsonify({"error": "Analysis failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ──────────────────────────────────────────────────────────

def start_web(host: str = "0.0.0.0", port: int = 8080,
              auto_start_bot: bool = True):
    """Start the web dashboard and optionally launch the bot."""
    config = get_config()

    web_handler = WebLogHandler()
    web_handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
    logging.getLogger().addHandler(web_handler)

    if auth_enabled(config):
        logger.info("Dashboard auth ENABLED for user '%s'", config.DASHBOARD_USER)
    else:
        logger.warning(
            "Dashboard auth DISABLED — set DASHBOARD_PASSWORD in .env "
            "before exposing this port to a network")

    if auto_start_bot:
        _start_bot()
        logger.info("Bot started in background thread")

    logger.info("Dashboard available at http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
