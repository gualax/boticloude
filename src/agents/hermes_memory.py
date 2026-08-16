"""Persistent memory for the Hermes agent.

Hermes remembers three things across restarts:
  - lessons        : conditional rules distilled from experience
  - trade records  : the full context of every closed trade
  - rolling stats  : per-strategy and per-price-bucket win rates

Lessons are structured (not just prose) so they can be applied
programmatically to future signals without another model call.
"""
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
HERMES_DIR = os.path.join(DATA_DIR, "hermes")
MEMORY_FILE = os.path.join(HERMES_DIR, "memory.json")

MAX_TRADE_RECORDS = 400
MAX_LESSONS = 40

VALID_ACTIONS = {"avoid", "size_down", "size_up", "prefer"}
VALID_CONDITIONS = {
    "strategy", "price_below", "price_above", "edge_below", "edge_above",
    "confidence_below", "confidence_above", "liquidity_below",
    "liquidity_above", "market_contains",
}


@dataclass
class Lesson:
    """A conditional rule Hermes learned from experience."""
    id: str
    text: str
    when: dict                      # structured condition
    action: str                     # avoid | size_down | size_up | prefer
    confidence: float = 0.5         # 0-1, how much Hermes trusts this
    evidence_count: int = 1         # trades supporting it
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    updated: str = field(default_factory=lambda: datetime.now().isoformat())
    times_applied: int = 0

    def is_valid(self) -> bool:
        if self.action not in VALID_ACTIONS:
            return False
        if not isinstance(self.when, dict) or not self.when:
            return False
        if not all(k in VALID_CONDITIONS for k in self.when):
            return False
        return 0.0 <= self.confidence <= 1.0


@dataclass
class TradeRecord:
    """The full life of one position: why it was opened and how it ended."""
    token_id: str
    market_name: str
    strategy: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    cost: float
    pnl: float
    pnl_pct: float
    edge_at_entry: float
    confidence_at_entry: float
    liquidity_at_entry: float
    exit_reason: str
    held_seconds: float
    closed_at: float = field(default_factory=time.time)

    @property
    def won(self) -> bool:
        return self.pnl > 0


class HermesMemory:
    """Loads, mutates and persists everything Hermes knows."""

    def __init__(self):
        self.lessons: list[Lesson] = []
        self.trade_records: list[TradeRecord] = []
        self.reflection_count: int = 0
        self.last_reflection: Optional[str] = None
        self.trades_since_reflection: int = 0
        self._next_lesson_id: int = 1
        self.load()

    # ── Recording ───────────────────────────────────────────────────────
    def record_trade(self, record: TradeRecord):
        self.trade_records.append(record)
        self.trades_since_reflection += 1
        if len(self.trade_records) > MAX_TRADE_RECORDS:
            self.trade_records = self.trade_records[-MAX_TRADE_RECORDS:]
        self.save()

    def add_lesson(self, lesson: Lesson) -> bool:
        if not lesson.is_valid():
            logger.warning("Rejected invalid lesson: %s", lesson.text[:60])
            return False
        self.lessons.append(lesson)
        # Keep the most trusted lessons if we overflow
        if len(self.lessons) > MAX_LESSONS:
            self.lessons.sort(key=lambda l: l.confidence * l.evidence_count,
                              reverse=True)
            self.lessons = self.lessons[:MAX_LESSONS]
        return True

    def update_lesson(self, lesson_id: str, **changes) -> bool:
        for lesson in self.lessons:
            if lesson.id != lesson_id:
                continue
            for key, value in changes.items():
                if hasattr(lesson, key):
                    setattr(lesson, key, value)
            lesson.updated = datetime.now().isoformat()
            return True
        return False

    def remove_lesson(self, lesson_id: str) -> bool:
        before = len(self.lessons)
        self.lessons = [l for l in self.lessons if l.id != lesson_id]
        return len(self.lessons) < before

    def next_lesson_id(self) -> str:
        lesson_id = f"L{self._next_lesson_id}"
        self._next_lesson_id += 1
        return lesson_id

    # ── Derived statistics ──────────────────────────────────────────────
    def strategy_stats(self) -> dict:
        """Win rate and PnL per strategy."""
        stats: dict[str, dict] = {}
        for record in self.trade_records:
            entry = stats.setdefault(record.strategy, {
                "trades": 0, "wins": 0, "pnl": 0.0, "total_pct": 0.0,
            })
            entry["trades"] += 1
            entry["wins"] += 1 if record.won else 0
            entry["pnl"] += record.pnl
            entry["total_pct"] += record.pnl_pct

        for entry in stats.values():
            trades = entry["trades"]
            entry["win_rate"] = entry["wins"] / trades if trades else 0.0
            entry["avg_pnl"] = entry["pnl"] / trades if trades else 0.0
            entry["avg_pct"] = entry["total_pct"] / trades if trades else 0.0
            entry["pnl"] = round(entry["pnl"], 2)
            entry["avg_pnl"] = round(entry["avg_pnl"], 2)
            entry["avg_pct"] = round(entry["avg_pct"], 1)
            entry["win_rate"] = round(entry["win_rate"], 3)
            entry.pop("total_pct", None)
        return stats

    def price_bucket_stats(self) -> dict:
        """Win rate grouped by entry price band."""
        buckets = {
            "0.05-0.15": (0.05, 0.15),
            "0.15-0.35": (0.15, 0.35),
            "0.35-0.65": (0.35, 0.65),
            "0.65-0.95": (0.65, 0.95),
        }
        stats = {name: {"trades": 0, "wins": 0, "pnl": 0.0}
                 for name in buckets}
        for record in self.trade_records:
            for name, (low, high) in buckets.items():
                if low <= record.entry_price < high:
                    stats[name]["trades"] += 1
                    stats[name]["wins"] += 1 if record.won else 0
                    stats[name]["pnl"] += record.pnl
                    break
        for entry in stats.values():
            trades = entry["trades"]
            entry["win_rate"] = round(entry["wins"] / trades, 3) if trades else 0.0
            entry["pnl"] = round(entry["pnl"], 2)
        return stats

    def summary(self) -> dict:
        total = len(self.trade_records)
        wins = sum(1 for r in self.trade_records if r.won)
        pnl = sum(r.pnl for r in self.trade_records)
        return {
            "total_trades_learned_from": total,
            "win_rate": round(wins / total, 3) if total else 0.0,
            "cumulative_pnl": round(pnl, 2),
            "lessons": len(self.lessons),
            "reflections": self.reflection_count,
            "last_reflection": self.last_reflection,
            "trades_since_reflection": self.trades_since_reflection,
        }

    # ── Persistence ─────────────────────────────────────────────────────
    def save(self):
        os.makedirs(HERMES_DIR, exist_ok=True)
        state = {
            "lessons": [asdict(l) for l in self.lessons],
            "trade_records": [asdict(r) for r in self.trade_records],
            "reflection_count": self.reflection_count,
            "last_reflection": self.last_reflection,
            "trades_since_reflection": self.trades_since_reflection,
            "next_lesson_id": self._next_lesson_id,
        }
        # Atomic write so a crash mid-save cannot corrupt the memory
        try:
            fd, tmp_path = tempfile.mkstemp(dir=HERMES_DIR, suffix=".tmp")
            with os.fdopen(fd, "w") as handle:
                json.dump(state, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_path, MEMORY_FILE)
        except Exception as e:
            logger.error("Failed to save Hermes memory: %s", e)

    def load(self):
        if not os.path.exists(MEMORY_FILE):
            return
        try:
            with open(MEMORY_FILE) as handle:
                state = json.load(handle)
            self.lessons = [Lesson(**l) for l in state.get("lessons", [])]
            self.trade_records = [
                TradeRecord(**r) for r in state.get("trade_records", [])
            ]
            self.reflection_count = state.get("reflection_count", 0)
            self.last_reflection = state.get("last_reflection")
            self.trades_since_reflection = state.get("trades_since_reflection", 0)
            self._next_lesson_id = state.get("next_lesson_id", 1)
            logger.info("Hermes memory loaded: %d lessons, %d trade records",
                        len(self.lessons), len(self.trade_records))
        except Exception as e:
            logger.warning("Could not load Hermes memory: %s", e)
