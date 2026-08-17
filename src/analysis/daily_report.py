"""Daily self-analysis agent powered by Google Gemini.

At the end of each day (configurable hour), collects all trading data,
sends it to Gemini for analysis, and auto-applies safe parameter adjustments.
"""
import json
import logging
import os
import re
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

## Auto-Ajustes
IMPORTANT: Output a JSON code block with parameter changes you recommend.
Only include parameters you want to change. Use EXACTLY this format:

```json
{"parameter_changes": [
  {"param": "PARAM_NAME", "old": current_value, "new": recommended_value, "reason": "brief reason"}
]}
```

Available parameters you can adjust:
{tunable_params}

Be conservative — only adjust parameters where data clearly supports it. \
Small incremental changes only (max 20-30% change from current value). \
Be specific with numbers. Reference actual trades and markets.\
"""


class DailyAnalyzer:
    """Collects daily data and sends to Gemini for self-analysis."""

    def __init__(self, config):
        self.config = config
        self._last_analysis_date: Optional[str] = None
        self._latest_report: Optional[dict] = None
        self._load_latest_report()

    def _get_gemini_model(self):
        """Return a callable that takes a prompt and returns a response, or None."""
        try:
            from google import genai
        except ImportError:
            logger.error("google-genai not installed. Run: pip install google-genai")
            return None

        try:
            client = genai.Client(api_key=self.config.GEMINI_API_KEY)
        except Exception as e:
            logger.error("Failed to init Gemini: %s", e)
            return None

        model_name = self.config.HERMES_MODEL

        class _Model:
            @staticmethod
            def generate_content(prompt: str):
                return client.models.generate_content(
                    model=model_name, contents=prompt)

        return _Model()

    def should_run(self) -> bool:
        """Check if it's time for daily analysis."""
        if not self.config.GEMINI_API_KEY:
            return False

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if self._last_analysis_date == today:
            return False

        return now.hour >= self.config.ANALYSIS_HOUR

    def collect_daily_data(self, bot) -> dict:
        """Collect all trading data for analysis."""
        summary = bot.paper.get_summary()

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
            "MIN_PRICE": self.config.MIN_PRICE,
            "MAX_PRICE": self.config.MAX_PRICE,
            "LOSS_COOLDOWN_CYCLES": self.config.LOSS_COOLDOWN_CYCLES,
            "MAX_EXPOSURE_PER_MARKET": self.config.MAX_EXPOSURE_PER_MARKET,
        }

        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "cycles_run": bot.cycle_count,
            "summary": summary,
            "trades_today": today_trades,
            "open_positions": positions,
            "config": config_snapshot,
        }

    def _build_tunable_description(self) -> str:
        """Build a description of tunable params with their current values and limits."""
        lines = []
        for param, limits in self.config.TUNABLE_PARAMS.items():
            current = getattr(self.config, param, "?")
            lines.append(
                f"- {param}: current={current}, min={limits['min']}, max={limits['max']}"
            )
        return "\n".join(lines)

    def _extract_parameter_changes(self, analysis_text: str) -> list[dict]:
        """Extract parameter changes from Gemini's JSON block."""
        # Find JSON code block
        pattern = r'```json\s*(\{.*?"parameter_changes".*?\})\s*```'
        match = re.search(pattern, analysis_text, re.DOTALL)
        if not match:
            return []

        try:
            data = json.loads(match.group(1))
            return data.get("parameter_changes", [])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse Gemini parameter changes: %s", e)
            return []

    def _apply_parameter_changes(self, changes: list[dict]) -> list[dict]:
        """Validate and apply parameter changes within safety limits.

        Returns list of actually applied changes.
        """
        applied = []
        tunable = self.config.TUNABLE_PARAMS

        for change in changes:
            param = change.get("param", "")
            new_val = change.get("new")
            reason = change.get("reason", "")

            # Must be a known tunable parameter
            if param not in tunable:
                logger.warning("Gemini suggested unknown param '%s', skipping", param)
                continue

            limits = tunable[param]
            old_val = getattr(self.config, param, None)
            if old_val is None:
                continue

            # Cast to correct type
            try:
                new_val = limits["type"](new_val)
            except (ValueError, TypeError):
                logger.warning("Invalid value for %s: %s", param, new_val)
                continue

            # Enforce safety bounds
            clamped = max(limits["min"], min(limits["max"], new_val))
            if clamped != new_val:
                logger.info("Clamped %s from %s to %s (safety limits)",
                            param, new_val, clamped)
                new_val = clamped

            # Skip if no meaningful change
            if abs(new_val - old_val) < 1e-6:
                continue

            # Apply the change
            setattr(self.config, param, new_val)
            applied.append({
                "param": param,
                "old": old_val,
                "new": new_val,
                "reason": reason,
            })
            logger.info("AUTO-TUNED %s: %s → %s (%s)", param, old_val, new_val, reason)

        return applied

    def run_analysis(self, bot) -> Optional[dict]:
        """Run the daily Gemini analysis and auto-apply recommendations."""
        logger.info("Starting daily AI analysis...")

        data = self.collect_daily_data(bot)

        model = self._get_gemini_model()
        if not model:
            return None

        # Build prompt with tunable params description
        system = SYSTEM_PROMPT.replace(
            "{tunable_params}", self._build_tunable_description()
        )

        prompt = (
            f"Here is today's trading bot data:\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```\n\n"
            f"Analyze this data and provide your report."
        )

        try:
            response = model.generate_content(system + "\n\n" + prompt)
            analysis_text = response.text
        except Exception as e:
            logger.error("Gemini API call failed: %s", e)
            return None

        # Extract and apply parameter changes
        changes = self._extract_parameter_changes(analysis_text)
        applied = self._apply_parameter_changes(changes) if changes else []

        report = {
            "date": data["date"],
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "summary_snapshot": data["summary"],
            "trades_count": len(data["trades_today"]),
            "analysis": analysis_text,
            "applied_changes": applied,
        }

        self._latest_report = report
        self._last_analysis_date = data["date"]
        self._save_report(report)

        if applied:
            logger.info("Daily analysis applied %d parameter changes", len(applied))
        else:
            logger.info("Daily analysis complete — no parameter changes applied")

        return report

    def get_latest_report(self) -> Optional[dict]:
        """Return the most recent analysis report."""
        return self._latest_report

    def get_all_reports(self) -> list[dict]:
        """Return list of all saved report summaries."""
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
                    "changes_applied": len(report.get("applied_changes", [])),
                })
            except Exception:
                continue
        return reports[:30]

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
