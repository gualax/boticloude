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

# What the bot is doing right now, so the UI never looks frozen
_activity: dict = {"phase": "stopped", "since": None, "next_cycle": None}

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
            _activity.update(phase="scanning", since=time.time(), next_cycle=None)
            result = _bot.run_cycle()

            # run_cycle already scanned; reuse its signals instead of
            # hitting the API a second time for the same data
            _last_signals = [_serialize_signal(s)
                             for s in result.get("signals", [])[:20]]

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

        interval = _bot.config.REBALANCE_INTERVAL
        _activity.update(phase="sleeping", since=time.time(),
                         next_cycle=time.time() + interval)
        # Wake promptly when stopped instead of finishing the whole sleep
        for _ in range(interval):
            if not (_bot and _bot.is_running):
                break
            time.sleep(1)

    _activity.update(phase="stopped", since=time.time(), next_cycle=None)


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

    # Live activity so the dashboard can show progress between cycles
    phase = _activity["phase"] if _bot.is_running else "stopped"
    summary["phase"] = phase
    summary["cycle_interval"] = _bot.config.REBALANCE_INTERVAL
    next_cycle = _activity.get("next_cycle")
    summary["seconds_to_next_cycle"] = (
        max(0, int(next_cycle - time.time())) if next_cycle else None)
    since = _activity.get("since")
    summary["seconds_in_phase"] = int(time.time() - since) if since else None
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
    if _start_bot():
        return jsonify({"status": "started",
                        "message": "Bot arrancado. Primer ciclo en curso."})
    return jsonify({"status": "already_running",
                    "message": "El bot ya estaba corriendo."})


@app.route("/api/bot/stop", methods=["POST"])
@guard
def api_bot_stop():
    if not _bot or not _bot.is_running:
        return jsonify({"status": "already_stopped",
                        "message": "El bot ya estaba parado."})
    _bot.is_running = False
    return jsonify({"status": "stopped",
                    "message": "Bot detenido. Las posiciones abiertas se mantienen."})


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

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class InsecureExposureError(RuntimeError):
    """Raised rather than serving an unauthenticated dashboard publicly."""


def check_exposure_is_safe(config, host: str):
    """Refuse to serve the control panel to a network without a password.

    The dashboard starts and stops the bot and shows the whole portfolio,
    so binding it to a public interface with no login would hand control
    to anyone who port-scans the machine.
    """
    if host in LOCAL_HOSTS or auth_enabled(config):
        return
    raise InsecureExposureError(
        f"Negandome a servir en {host} sin contrasena.\n\n"
        "El panel arranca y detiene el bot y muestra toda la cartera, "
        "asi que exponerlo sin login\nse la entrega a cualquiera que "
        "escanee la maquina.\n\n"
        "Pon DASHBOARD_PASSWORD en .env, o usa --host 127.0.0.1 para "
        "servir solo en local\n(y llega por un tunel SSH)."
    )


def require_password_for_production(config):
    """Serving under gunicorn always means serving a network.

    Nobody reaches for a production WSGI server to talk to their own
    loopback, so an empty password here is always a mistake.
    """
    if not config.DASHBOARD_PASSWORD:
        raise InsecureExposureError(
            "DASHBOARD_PASSWORD esta vacia.\n"
            "Bajo gunicorn el panel se sirve a la red, y sin contrasena "
            "quedaria abierto a cualquiera.\n"
            "Ponla en .env antes de arrancar."
        )


def prepare(auto_start_bot: bool = True):
    """Wire up logging and launch the bot. Shared by dev and production."""
    config = get_config()

    web_handler = WebLogHandler()
    web_handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
    logging.getLogger().addHandler(web_handler)

    if auth_enabled(config):
        logger.info("Dashboard auth ENABLED for user '%s'", config.DASHBOARD_USER)
    else:
        logger.warning("Dashboard auth DISABLED — solo seguro en localhost")

    if auto_start_bot:
        _start_bot()
        logger.info("Bot started in background thread")


def start_web(host: str = "127.0.0.1", port: int = 8080,
              auto_start_bot: bool = True):
    """Start the development server. Production runs under gunicorn."""
    config = get_config()
    check_exposure_is_safe(config, host)

    prepare(auto_start_bot=auto_start_bot)

    if host not in LOCAL_HOSTS:
        logger.warning(
            "Servidor de desarrollo expuesto en %s. Para uso permanente "
            "usa gunicorn (ver README).", host)

    logger.info("Dashboard available at http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
