"""Hermes — a trading agent that learns from its own experience.

Hermes sits between signal generation and order execution. It does three
things, and it gets better at all of them the longer the bot runs:

  1. Remembers  — records the full context of every trade it sees close.
  2. Reflects   — periodically asks Gemini to distill those outcomes into
                  structured, conditional lessons.
  3. Decides    — applies those lessons (plus hard statistics) to veto or
                  resize new signals before any money moves.

The lessons are structured data, not prose, so decisions cost nothing at
runtime: no model call is made on the hot path.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.agents.hermes_memory import HermesMemory, Lesson, TradeRecord

logger = logging.getLogger(__name__)


REFLECTION_PROMPT = """\
You are Hermes, the learning core of an automated Polymarket prediction-market
trading bot. You are reviewing the bot's recent closed trades in order to
extract durable, reusable trading rules.

You already hold these lessons:
{current_lessons}

Rolling performance by strategy:
{strategy_stats}

Performance by entry-price band:
{price_stats}

Here are the trades that closed since your last reflection:
{recent_trades}

Your job is to update your rule set. Think about WHY trades won or lost, not
just that they did. Look for conditions that repeat: a strategy that only works
in a price band, an edge threshold below which nothing pays, a market type that
keeps burning capital.

Rules:
- Only propose a lesson when at least 3 trades support it. Say so in evidence_count.
- Prefer updating an existing lesson over adding a near-duplicate.
- Remove a lesson when new evidence contradicts it.
- Be conservative with "avoid": it blocks trades entirely.
- confidence reflects how strongly the data supports the rule (0.0 to 1.0).

Condition keys you may use in "when" (combine freely, all must match):
  strategy            exact strategy name
  price_below         entry price strictly below this
  price_above         entry price strictly above this
  edge_below          signal edge below this (as a decimal, e.g. 0.05)
  edge_above          signal edge above this
  confidence_below    signal confidence below this
  confidence_above    signal confidence above this
  liquidity_below     market liquidity below this (USD)
  liquidity_above     market liquidity above this (USD)
  market_contains     case-insensitive substring of the market question

Actions you may use:
  avoid      do not take the trade at all
  size_down  take it at half size
  size_up    take it at 1.3x size
  prefer     take it at 1.15x size

Write every "text" field and the "insight" IN SPANISH — they are shown directly
in the dashboard. Keep each "text" under 140 characters.

Respond with a short paragraph of reasoning IN SPANISH, then exactly one JSON
block in this format:

```json
{{
  "lessons_to_add": [
    {{"text": "...", "when": {{"strategy": "momentum", "price_below": 0.15}},
      "action": "avoid", "confidence": 0.7, "evidence_count": 5}}
  ],
  "lessons_to_update": [
    {{"id": "L1", "confidence": 0.85, "evidence_count": 12}}
  ],
  "lessons_to_remove": ["L3"],
  "insight": "One sentence in Spanish on what you learned this cycle."
}}
```

If you have nothing to change, return empty lists and still give the insight.
"""


@dataclass
class HermesVerdict:
    """Hermes' ruling on a single candidate trade."""
    approved: bool = True
    size_multiplier: float = 1.0
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.reasons:
            return "sin objeciones"
        return "; ".join(self.reasons)


class HermesAgent:
    """The learning agent. Owns its memory and its reflection cycle."""

    def __init__(self, config):
        self.config = config
        self.memory = HermesMemory()
        # token_id -> the signal context that opened the position
        self._open_context: dict[str, dict] = {}
        self._last_insight: Optional[str] = None
        self._verdict_log: list[dict] = []

    # ── 1. Remembering ──────────────────────────────────────────────────
    def note_entry(self, token_id: str, signal, size: float):
        """Store why a position was opened, so we can judge it on exit."""
        self._open_context[token_id] = {
            "strategy": signal.strategy,
            "edge": signal.edge,
            "confidence": signal.confidence,
            "liquidity": signal.liquidity,
            "market_name": signal.market_name,
            "side": signal.side,
            "entry_price": signal.current_price,
            "size": size,
            "opened_at": time.time(),
        }

    def note_exit(self, token_id: str, position, exit_price: float,
                  pnl: float, exit_reason: str):
        """Record the completed round trip into long-term memory."""
        context = self._open_context.pop(token_id, None)
        if context is None:
            # Position predates this Hermes instance — record what we can
            context = {
                "strategy": "unknown",
                "edge": 0.0,
                "confidence": 0.0,
                "liquidity": 0.0,
                "opened_at": getattr(position, "timestamp", time.time()),
            }

        cost = position.cost if position.cost else 1e-9
        record = TradeRecord(
            token_id=token_id,
            market_name=position.market_name,
            strategy=context["strategy"],
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            size=position.size,
            cost=position.cost,
            pnl=pnl,
            pnl_pct=pnl / cost * 100,
            edge_at_entry=context["edge"],
            confidence_at_entry=context["confidence"],
            liquidity_at_entry=context["liquidity"],
            exit_reason=exit_reason,
            held_seconds=time.time() - context["opened_at"],
        )
        self.memory.record_trade(record)
        logger.info("Hermes recorded %s trade on '%s': PnL $%.2f (%s)",
                    record.strategy, record.market_name[:40], pnl, exit_reason)

    # ── 2. Deciding ─────────────────────────────────────────────────────
    def evaluate_signal(self, signal) -> HermesVerdict:
        """Apply learned lessons and hard statistics to a candidate trade."""
        verdict = HermesVerdict()
        if not self.config.HERMES_ENABLED:
            return verdict

        # a) Hard statistics override everything: a strategy that has proven
        #    itself a loser over a real sample gets shut off.
        stats = self.memory.strategy_stats().get(signal.strategy)
        min_sample = self.config.HERMES_MIN_SAMPLE
        if stats and stats["trades"] >= min_sample:
            if stats["win_rate"] < self.config.HERMES_VETO_WIN_RATE:
                verdict.approved = False
                verdict.reasons.append(
                    f"{signal.strategy} gana solo {stats['win_rate']:.0%} "
                    f"en {stats['trades']} trades"
                )
                self._log_verdict(signal, verdict)
                return verdict
            if stats["avg_pnl"] < 0:
                verdict.size_multiplier *= 0.6
                verdict.reasons.append(
                    f"{signal.strategy} promedia ${stats['avg_pnl']:.2f} por trade"
                )

        # b) Learned lessons
        for lesson in self.memory.lessons:
            if lesson.confidence < self.config.HERMES_MIN_LESSON_CONFIDENCE:
                continue
            if not self._matches(lesson.when, signal):
                continue

            lesson.times_applied += 1
            if lesson.action == "avoid":
                verdict.approved = False
                verdict.reasons.append(f"[{lesson.id}] {lesson.text}")
                break
            if lesson.action == "size_down":
                verdict.size_multiplier *= 0.5
                verdict.reasons.append(f"[{lesson.id}] {lesson.text}")
            elif lesson.action == "size_up":
                verdict.size_multiplier *= 1.3
                verdict.reasons.append(f"[{lesson.id}] {lesson.text}")
            elif lesson.action == "prefer":
                verdict.size_multiplier *= 1.15
                verdict.reasons.append(f"[{lesson.id}] {lesson.text}")

        # Never let stacked lessons blow the size up
        verdict.size_multiplier = max(0.25, min(1.5, verdict.size_multiplier))
        self._log_verdict(signal, verdict)
        return verdict

    @staticmethod
    def _matches(when: dict, signal) -> bool:
        """Check a structured lesson condition against a signal."""
        checks = {
            "strategy": lambda v: signal.strategy == v,
            "price_below": lambda v: signal.current_price < v,
            "price_above": lambda v: signal.current_price > v,
            "edge_below": lambda v: signal.edge < v,
            "edge_above": lambda v: signal.edge > v,
            "confidence_below": lambda v: signal.confidence < v,
            "confidence_above": lambda v: signal.confidence > v,
            "liquidity_below": lambda v: signal.liquidity < v,
            "liquidity_above": lambda v: signal.liquidity > v,
            "market_contains": lambda v: str(v).lower() in signal.market_name.lower(),
        }
        for key, value in when.items():
            check = checks.get(key)
            if check is None:
                return False
            try:
                if not check(value):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def _log_verdict(self, signal, verdict: HermesVerdict):
        if verdict.approved and verdict.size_multiplier == 1.0:
            return  # nothing interesting happened
        self._verdict_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "market": signal.market_name[:60],
            "strategy": signal.strategy,
            "approved": verdict.approved,
            "multiplier": round(verdict.size_multiplier, 2),
            "reason": verdict.summary,
        })
        self._verdict_log = self._verdict_log[-50:]

    # ── 3. Reflecting ───────────────────────────────────────────────────
    def should_reflect(self) -> bool:
        if not self.config.HERMES_ENABLED or not self.config.GEMINI_API_KEY:
            return False
        return (self.memory.trades_since_reflection
                >= self.config.HERMES_REFLECT_EVERY_N_TRADES)

    def reflect(self) -> Optional[dict]:
        """Ask Gemini to turn recent outcomes into structured lessons."""
        if not self.config.GEMINI_API_KEY:
            return None

        count = max(self.memory.trades_since_reflection, 1)
        recent = self.memory.trade_records[-min(count, 40):]
        if not recent:
            return None

        model = self._get_model()
        if model is None:
            return None

        prompt = REFLECTION_PROMPT.format(
            current_lessons=self._format_lessons(),
            strategy_stats=json.dumps(self.memory.strategy_stats(), indent=2),
            price_stats=json.dumps(self.memory.price_bucket_stats(), indent=2),
            recent_trades=json.dumps(
                [self._trade_for_prompt(r) for r in recent], indent=2,
                ensure_ascii=False),
        )

        try:
            response = model.generate_content(prompt)
            text = response.text
        except Exception as e:
            logger.error("Hermes reflection failed: %s", e)
            return None

        changes = self._apply_reflection(text)

        self.memory.reflection_count += 1
        self.memory.last_reflection = datetime.now().isoformat()
        self.memory.trades_since_reflection = 0
        self.memory.save()

        logger.info(
            "Hermes reflected on %d trades: +%d lessons, ~%d updated, -%d removed",
            len(recent), changes["added"], changes["updated"], changes["removed"],
        )
        return {
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trades_reviewed": len(recent),
            "insight": self._last_insight,
            "raw": text,
            **changes,
        }

    def _get_model(self):
        """Return a callable that takes a prompt and returns text, or None."""
        try:
            from google import genai
        except ImportError:
            logger.error("google-genai not installed. Run: pip install google-genai")
            return None

        try:
            client = genai.Client(api_key=self.config.GEMINI_API_KEY)
        except Exception as e:
            logger.error("Hermes could not reach Gemini: %s", e)
            return None

        model_name = self.config.HERMES_MODEL

        class _Model:
            @staticmethod
            def generate_content(prompt: str):
                return client.models.generate_content(
                    model=model_name, contents=prompt)

        return _Model()

    def _format_lessons(self) -> str:
        if not self.memory.lessons:
            return "(none yet — this is your first reflection)"
        return json.dumps([
            {
                "id": l.id, "text": l.text, "when": l.when,
                "action": l.action, "confidence": l.confidence,
                "evidence_count": l.evidence_count,
            }
            for l in self.memory.lessons
        ], indent=2, ensure_ascii=False)

    @staticmethod
    def _trade_for_prompt(record: TradeRecord) -> dict:
        return {
            "market": record.market_name[:80],
            "strategy": record.strategy,
            "side": record.side,
            "entry": round(record.entry_price, 4),
            "exit": round(record.exit_price, 4),
            "edge_at_entry": round(record.edge_at_entry, 4),
            "confidence_at_entry": round(record.confidence_at_entry, 2),
            "liquidity": round(record.liquidity_at_entry, 0),
            "pnl": round(record.pnl, 2),
            "pnl_pct": round(record.pnl_pct, 1),
            "exit_reason": record.exit_reason,
            "held_hours": round(record.held_seconds / 3600, 1),
        }

    def _apply_reflection(self, text: str) -> dict:
        """Parse and safely apply Gemini's proposed lesson changes."""
        result = {"added": 0, "updated": 0, "removed": 0}
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not match:
            logger.warning("Hermes reflection had no JSON block")
            return result

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.warning("Hermes reflection JSON invalid: %s", e)
            return result

        self._last_insight = payload.get("insight")

        for raw in payload.get("lessons_to_add", []) or []:
            lesson = Lesson(
                id=self.memory.next_lesson_id(),
                text=str(raw.get("text", ""))[:300],
                when=raw.get("when", {}) or {},
                action=str(raw.get("action", "")),
                confidence=self._clamp(raw.get("confidence", 0.5), 0.0, 1.0),
                evidence_count=int(raw.get("evidence_count", 1) or 1),
            )
            if lesson.evidence_count < self.config.HERMES_MIN_EVIDENCE:
                logger.info("Hermes skipped thin lesson (%d samples): %s",
                            lesson.evidence_count, lesson.text[:60])
                continue
            if self.memory.add_lesson(lesson):
                result["added"] += 1
                logger.info("Hermes learned [%s] %s", lesson.id, lesson.text[:80])

        for raw in payload.get("lessons_to_update", []) or []:
            lesson_id = raw.get("id")
            changes = {}
            if "confidence" in raw:
                changes["confidence"] = self._clamp(raw["confidence"], 0.0, 1.0)
            if "evidence_count" in raw:
                try:
                    changes["evidence_count"] = int(raw["evidence_count"])
                except (TypeError, ValueError):
                    pass
            if "text" in raw:
                changes["text"] = str(raw["text"])[:300]
            if changes and self.memory.update_lesson(lesson_id, **changes):
                result["updated"] += 1

        for lesson_id in payload.get("lessons_to_remove", []) or []:
            if self.memory.remove_lesson(str(lesson_id)):
                result["removed"] += 1
                logger.info("Hermes discarded lesson %s", lesson_id)

        return result

    @staticmethod
    def _clamp(value, low: float, high: float) -> float:
        try:
            return max(low, min(high, float(value)))
        except (TypeError, ValueError):
            return low

    # ── Introspection for the dashboard ─────────────────────────────────
    def get_state(self) -> dict:
        return {
            "enabled": self.config.HERMES_ENABLED,
            "has_brain": bool(self.config.GEMINI_API_KEY),
            "model": self.config.HERMES_MODEL,
            "summary": self.memory.summary(),
            "strategy_stats": self.memory.strategy_stats(),
            "price_stats": self.memory.price_bucket_stats(),
            "last_insight": self._last_insight,
            "lessons": [
                {
                    "id": l.id,
                    "text": l.text,
                    "when": l.when,
                    "action": l.action,
                    "confidence": round(l.confidence, 2),
                    "evidence_count": l.evidence_count,
                    "times_applied": l.times_applied,
                }
                for l in sorted(self.memory.lessons,
                                key=lambda x: x.confidence, reverse=True)
            ],
            "recent_verdicts": list(reversed(self._verdict_log[-20:])),
            "reflect_due_in": max(
                0,
                self.config.HERMES_REFLECT_EVERY_N_TRADES
                - self.memory.trades_since_reflection,
            ),
        }
