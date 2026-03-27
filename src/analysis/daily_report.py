"""Daily self-analysis agent powered by Google Gemini.

At the end of each day (configurable hour), collects all trading data,
sends it to Gemini for analysis, and stores actionable recommendations.
"""
import json
import logging
import os
import time
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")


SYSTEM_PROMPT = """\
You are an expert quantitative trading analyst reviewing a Polymarket \
prediction-market trading bot. You will receive the bot's daily performance \
data and must provide a concise, actionable analysis IN SPANISH.

Your analysis must include exactly these sections:

## Resumen del Dia
Brief 2-3 sentence summary of overall performance.

## Metricas Clave
Key numbers: return %, win rate, avg edge captured, best/worst trade.

## Que Funciono
Which strategies and trades performed well, and why.

## Que No Funciono
Which strategies underperformed, losing trades, missed opportunities.

## Recomendaciones
3-5 specific, actionable parameter adjustments. For each one, state:
- The current value
- The recommended new value
- Expected impact

## Puntuacion
Rate today's performance 1-10 with brief justification.

Be specific with numbers. Do not be generic. Reference actual trades and markets.\
"""


class DailyAnalyzer:
    """Collects daily data and sends to Gemini for self-analysis."""

    def __init__(self, config):
        self.config = config
        self._last_analysis_date: Optional[str] = None
        self._latest_report: Optional[dict] = None
        self._load_latest_report()

    def _get_gemini_model(self):
        """Initialize Gemini client lazily."""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.config.GEMINI_API_KEY)
            return genai.GenerativeModel("gemini-2.5-pro")
        except ImportError:
            logger.error("google-generativeai not installed. "
                         "Run: pip install google-generativeai")
            return None
        except Exception as e:
            logger.error("Failed to init Gemini: %s", e)
            return None

    def should_run(self) -> bool:
        """Check if it's time for daily analysis."""
        if not self.config.GEMINI_API_KEY:
            return False

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        # Already ran today
        if self._last_analysis_date == today:
            return False

        # Run at configured hour
        return now.hour >= self.config.ANALYSIS_HOUR

    def collect_daily_data(self, bot) -> dict:
        """Collect all trading data for analysis."""
        summary = bot.paper.get_summary()

        # Trades from today
        today_start = datetime.combine(date.today(), datetime.min.time()).timestamp()
        today_trades = [
            {
                "market": t.market_name,
                "side": t.side,
                "action": t.action,
                "price": round(t.price, 4),
                "size": round(t.size, 1),
                "cost": round(t.cost, 2),
                "pnl": round(t.pnl, 2),
                "time": datetime.fromtimestamp(t.timestamp).strftime("%H:%M:%S"),
            }
            for t in bot.paper.trade_history
            if t.timestamp >= today_start
        ]

        # Current positions
        positions = [
            {
                "market": p.market_name,
                "side": p.side,
                "entry_price": round(p.entry_price, 4),
                "current_price": round(p.current_price, 4),
                "size": round(p.size, 1),
                "pnl": round(p.pnl, 2),
                "pnl_pct": round(p.pnl_pct, 1),
            }
            for p in bot.paper.positions.values()
        ]

        # Current config parameters
        config_snapshot = {
            "MIN_EDGE": self.config.MIN_EDGE,
            "MIN_LIQUIDITY": self.config.MIN_LIQUIDITY,
            "STOP_LOSS_PCT": self.config.STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT": self.config.TAKE_PROFIT_PCT,
            "TRAILING_STOP_ACTIVATION": self.config.TRAILING_STOP_ACTIVATION,
            "TRAILING_STOP_DISTANCE": self.config.TRAILING_STOP_DISTANCE,
            "KELLY_FRACTION": self.config.KELLY_FRACTION,
            "MAX_POSITION_SIZE": self.config.MAX_POSITION_SIZE,
            "MAX_TOTAL_EXPOSURE": self.config.MAX_TOTAL_EXPOSURE,
            "SLIPPAGE_BPS": self.config.SLIPPAGE_BPS,
            "REBALANCE_INTERVAL": self.config.REBALANCE_INTERVAL,
            "MISPRICING_THRESHOLD": self.config.MISPRICING_THRESHOLD,
            "MEAN_REVERSION_DEVIATION": self.config.MEAN_REVERSION_DEVIATION,
            "MOMENTUM_THRESHOLD": self.config.MOMENTUM_THRESHOLD,
        }

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "cycles_run": bot.cycle_count,
            "summary": summary,
            "trades_today": today_trades,
            "open_positions": positions,
            "config": config_snapshot,
        }

    def run_analysis(self, bot) -> Optional[dict]:
        """Run the daily Gemini analysis."""
        logger.info("Starting daily AI analysis...")

        data = self.collect_daily_data(bot)

        model = self._get_gemini_model()
        if not model:
            return None

        prompt = (
            f"Here is today's trading bot data:\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```\n\n"
            f"Analyze this data and provide your report."
        )

        try:
            response = model.generate_content(
                [{"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]}],
            )
            analysis_text = response.text
        except Exception as e:
            logger.error("Gemini API call failed: %s", e)
            return None

        report = {
            "date": data["date"],
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "summary_snapshot": data["summary"],
            "trades_count": len(data["trades_today"]),
            "analysis": analysis_text,
        }

        self._latest_report = report
        self._last_analysis_date = data["date"]
        self._save_report(report)

        logger.info("Daily AI analysis complete for %s", data["date"])
        return report

    def get_latest_report(self) -> Optional[dict]:
        """Return the most recent analysis report."""
        return self._latest_report

    def get_all_reports(self) -> list[dict]:
        """Return list of all saved report summaries (without full text)."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        reports = []
        for fname in sorted(os.listdir(REPORTS_DIR), reverse=True):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(REPORTS_DIR, fname)) as f:
                    report = json.load(f)
                reports.append({
                    "date": report["date"],
                    "time": report.get("time_str", ""),
                    "trades_count": report.get("trades_count", 0),
                    "portfolio_value": report.get("summary_snapshot", {}).get(
                        "portfolio_value", 0),
                })
            except Exception:
                continue
        return reports[:30]  # Last 30 days

    def _save_report(self, report: dict):
        """Persist report to disk."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        filename = f"report_{report['date']}.json"
        path = os.path.join(REPORTS_DIR, filename)
        try:
            with open(path, "w") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info("Report saved to %s", path)
        except Exception as e:
            logger.error("Failed to save report: %s", e)

    def _load_latest_report(self):
        """Load the most recent report from disk on startup."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        files = sorted(
            [f for f in os.listdir(REPORTS_DIR) if f.endswith(".json")],
            reverse=True,
        )
        if not files:
            return
        try:
            with open(os.path.join(REPORTS_DIR, files[0])) as f:
                self._latest_report = json.load(f)
            self._last_analysis_date = self._latest_report.get("date")
            logger.info("Loaded latest report from %s", files[0])
        except Exception as e:
            logger.warning("Could not load latest report: %s", e)
