"""Tests for the Hermes learning agent."""
import json
import time
from dataclasses import dataclass

import pytest

from config import Config
from src.agents import hermes_memory
from src.agents.hermes import HermesAgent
from src.agents.hermes_memory import HermesMemory, Lesson, TradeRecord


@dataclass
class FakeSignal:
    token_id: str = "t1"
    market_name: str = "Will Bitcoin reach $150,000?"
    strategy: str = "momentum"
    side: str = "Yes"
    current_price: float = 0.30
    fair_value: float = 0.45
    edge: float = 0.15
    confidence: float = 0.6
    liquidity: float = 1000.0


@dataclass
class FakePosition:
    token_id: str = "t1"
    market_name: str = "Will Bitcoin reach $150,000?"
    side: str = "Yes"
    entry_price: float = 0.30
    size: float = 100.0
    cost: float = 30.0
    current_price: float = 0.20
    timestamp: float = 0.0


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Point Hermes at a throwaway memory file for every test."""
    monkeypatch.setattr(hermes_memory, "HERMES_DIR", str(tmp_path))
    monkeypatch.setattr(hermes_memory, "MEMORY_FILE",
                        str(tmp_path / "memory.json"))


@pytest.fixture
def agent():
    config = Config()
    config.GEMINI_API_KEY = ""       # never call the network in tests
    config.HERMES_ENABLED = True
    return HermesAgent(config)


def make_record(strategy="momentum", pnl=-5.0, entry=0.30, **kwargs):
    defaults = dict(
        token_id="t1", market_name="Test market", strategy=strategy,
        side="Yes", entry_price=entry, exit_price=entry + pnl / 100,
        size=100.0, cost=entry * 100, pnl=pnl,
        pnl_pct=pnl / (entry * 100) * 100, edge_at_entry=0.10,
        confidence_at_entry=0.5, liquidity_at_entry=1000.0,
        exit_reason="stop-loss", held_seconds=3600.0,
    )
    defaults.update(kwargs)
    return TradeRecord(**defaults)


class TestMemory:
    def test_starts_empty(self):
        memory = HermesMemory()
        assert memory.lessons == []
        assert memory.trade_records == []

    def test_records_and_persists_trades(self):
        memory = HermesMemory()
        memory.record_trade(make_record())
        assert len(memory.trade_records) == 1

        # A fresh instance must see the same data on disk
        reloaded = HermesMemory()
        assert len(reloaded.trade_records) == 1
        assert reloaded.trade_records[0].strategy == "momentum"

    def test_strategy_stats(self):
        memory = HermesMemory()
        memory.record_trade(make_record(pnl=10.0))
        memory.record_trade(make_record(pnl=-4.0))
        memory.record_trade(make_record(pnl=-6.0))

        stats = memory.strategy_stats()["momentum"]
        assert stats["trades"] == 3
        assert stats["wins"] == 1
        assert stats["win_rate"] == pytest.approx(1 / 3, abs=0.01)
        assert stats["pnl"] == pytest.approx(0.0, abs=0.01)

    def test_price_bucket_stats(self):
        memory = HermesMemory()
        memory.record_trade(make_record(entry=0.10, pnl=-3.0))
        memory.record_trade(make_record(entry=0.50, pnl=8.0))
        stats = memory.price_bucket_stats()
        assert stats["0.05-0.15"]["trades"] == 1
        assert stats["0.35-0.65"]["trades"] == 1
        assert stats["0.35-0.65"]["win_rate"] == 1.0

    def test_rejects_invalid_lessons(self):
        memory = HermesMemory()
        bad_action = Lesson(id="L1", text="x", when={"strategy": "a"},
                            action="delete_everything")
        assert memory.add_lesson(bad_action) is False

        bad_condition = Lesson(id="L2", text="x", when={"rm_rf": "/"},
                               action="avoid")
        assert memory.add_lesson(bad_condition) is False

        empty_condition = Lesson(id="L3", text="x", when={}, action="avoid")
        assert memory.add_lesson(empty_condition) is False

        assert memory.lessons == []

    def test_accepts_valid_lesson(self):
        memory = HermesMemory()
        good = Lesson(id="L1", text="Momentum fails on cheap tokens",
                      when={"strategy": "momentum", "price_below": 0.15},
                      action="avoid", confidence=0.8, evidence_count=6)
        assert memory.add_lesson(good) is True
        assert len(memory.lessons) == 1


class TestLessonMatching:
    def test_matches_on_strategy(self, agent):
        assert agent._matches({"strategy": "momentum"}, FakeSignal())
        assert not agent._matches({"strategy": "mispricing"}, FakeSignal())

    def test_matches_on_price(self, agent):
        assert agent._matches({"price_below": 0.50}, FakeSignal(current_price=0.30))
        assert not agent._matches({"price_below": 0.20}, FakeSignal(current_price=0.30))

    def test_all_conditions_must_hold(self, agent):
        signal = FakeSignal(strategy="momentum", current_price=0.30)
        assert agent._matches({"strategy": "momentum", "price_below": 0.40}, signal)
        assert not agent._matches(
            {"strategy": "momentum", "price_below": 0.10}, signal)

    def test_market_contains_is_case_insensitive(self, agent):
        assert agent._matches({"market_contains": "bitcoin"}, FakeSignal())

    def test_unknown_condition_never_matches(self, agent):
        assert not agent._matches({"bogus_key": 1}, FakeSignal())


class TestVerdicts:
    def test_approves_by_default(self, agent):
        verdict = agent.evaluate_signal(FakeSignal())
        assert verdict.approved
        assert verdict.size_multiplier == 1.0

    def test_avoid_lesson_vetoes(self, agent):
        agent.memory.add_lesson(Lesson(
            id="L1", text="Momentum loses on cheap tokens",
            when={"strategy": "momentum", "price_below": 0.40},
            action="avoid", confidence=0.9, evidence_count=8))
        verdict = agent.evaluate_signal(FakeSignal(current_price=0.30))
        assert verdict.approved is False
        assert "L1" in verdict.summary

    def test_size_down_lesson_halves_position(self, agent):
        agent.memory.add_lesson(Lesson(
            id="L1", text="Trim momentum", when={"strategy": "momentum"},
            action="size_down", confidence=0.7, evidence_count=5))
        verdict = agent.evaluate_signal(FakeSignal())
        assert verdict.approved
        assert verdict.size_multiplier == pytest.approx(0.5)

    def test_low_confidence_lesson_is_ignored(self, agent):
        agent.memory.add_lesson(Lesson(
            id="L1", text="Weak hunch", when={"strategy": "momentum"},
            action="avoid", confidence=0.1, evidence_count=3))
        assert agent.evaluate_signal(FakeSignal()).approved

    def test_multiplier_is_bounded(self, agent):
        for i in range(6):
            agent.memory.add_lesson(Lesson(
                id=f"L{i}", text="boost", when={"strategy": "momentum"},
                action="size_up", confidence=0.9, evidence_count=5))
        verdict = agent.evaluate_signal(FakeSignal())
        assert verdict.size_multiplier <= 1.5

    def test_losing_strategy_is_vetoed_once_sample_is_real(self, agent):
        # 20 trades at a 10% win rate — below the veto threshold
        for i in range(20):
            agent.memory.record_trade(
                make_record(pnl=8.0 if i < 2 else -5.0))
        verdict = agent.evaluate_signal(FakeSignal(strategy="momentum"))
        assert verdict.approved is False
        assert "momentum" in verdict.summary

    def test_small_sample_does_not_veto(self, agent):
        for _ in range(4):
            agent.memory.record_trade(make_record(pnl=-5.0))
        assert agent.evaluate_signal(FakeSignal()).approved

    def test_disabled_agent_always_approves(self, agent):
        agent.config.HERMES_ENABLED = False
        agent.memory.add_lesson(Lesson(
            id="L1", text="block", when={"strategy": "momentum"},
            action="avoid", confidence=1.0, evidence_count=9))
        assert agent.evaluate_signal(FakeSignal()).approved


class TestTradeLifecycle:
    def test_entry_and_exit_produce_a_record(self, agent):
        signal = FakeSignal()
        agent.note_entry("t1", signal, size=100)
        assert "t1" in agent._open_context

        agent.note_exit("t1", FakePosition(), exit_price=0.20,
                        pnl=-10.0, exit_reason="stop-loss")

        assert "t1" not in agent._open_context
        assert len(agent.memory.trade_records) == 1
        record = agent.memory.trade_records[0]
        assert record.strategy == "momentum"
        assert record.edge_at_entry == pytest.approx(0.15)
        assert record.pnl == -10.0
        assert record.won is False

    def test_exit_without_entry_still_records(self, agent):
        agent.note_exit("orphan", FakePosition(), exit_price=0.4,
                        pnl=5.0, exit_reason="take-profit")
        assert len(agent.memory.trade_records) == 1
        assert agent.memory.trade_records[0].strategy == "unknown"


class TestReflection:
    def test_reflection_threshold(self, agent):
        agent.config.GEMINI_API_KEY = "fake-key"
        agent.config.HERMES_REFLECT_EVERY_N_TRADES = 3
        assert not agent.should_reflect()
        for _ in range(3):
            agent.memory.record_trade(make_record())
        assert agent.should_reflect()

    def test_no_reflection_without_a_brain(self, agent):
        agent.config.GEMINI_API_KEY = ""
        for _ in range(50):
            agent.memory.record_trade(make_record())
        assert not agent.should_reflect()

    def test_applies_valid_lessons_from_response(self, agent):
        response = """Analisis del ciclo.

```json
{"lessons_to_add": [
  {"text": "Momentum pierde bajo $0.15",
   "when": {"strategy": "momentum", "price_below": 0.15},
   "action": "avoid", "confidence": 0.8, "evidence_count": 7}
],
 "lessons_to_update": [], "lessons_to_remove": [],
 "insight": "Los tokens baratos destruyen capital."}
```"""
        result = agent._apply_reflection(response)
        assert result["added"] == 1
        assert len(agent.memory.lessons) == 1
        assert agent._last_insight == "Los tokens baratos destruyen capital."

    def test_rejects_thin_evidence(self, agent):
        response = """```json
{"lessons_to_add": [
  {"text": "Corazonada", "when": {"strategy": "momentum"},
   "action": "avoid", "confidence": 0.9, "evidence_count": 1}
], "lessons_to_update": [], "lessons_to_remove": [], "insight": "x"}
```"""
        assert agent._apply_reflection(response)["added"] == 0
        assert agent.memory.lessons == []

    def test_rejects_invalid_action(self, agent):
        response = """```json
{"lessons_to_add": [
  {"text": "bad", "when": {"strategy": "momentum"},
   "action": "liquidate_everything", "confidence": 0.9, "evidence_count": 9}
], "lessons_to_update": [], "lessons_to_remove": [], "insight": "x"}
```"""
        assert agent._apply_reflection(response)["added"] == 0

    def test_clamps_out_of_range_confidence(self, agent):
        response = """```json
{"lessons_to_add": [
  {"text": "overconfident", "when": {"strategy": "momentum"},
   "action": "size_down", "confidence": 9.5, "evidence_count": 5}
], "lessons_to_update": [], "lessons_to_remove": [], "insight": "x"}
```"""
        agent._apply_reflection(response)
        assert agent.memory.lessons[0].confidence == 1.0

    def test_handles_missing_json_block(self, agent):
        result = agent._apply_reflection("Solo texto, sin JSON.")
        assert result == {"added": 0, "updated": 0, "removed": 0}

    def test_handles_malformed_json(self, agent):
        result = agent._apply_reflection("```json\n{not valid json,,}\n```")
        assert result["added"] == 0

    def test_removes_lessons(self, agent):
        agent.memory.add_lesson(Lesson(
            id="L1", text="old", when={"strategy": "momentum"},
            action="avoid", confidence=0.5, evidence_count=4))
        response = """```json
{"lessons_to_add": [], "lessons_to_update": [],
 "lessons_to_remove": ["L1"], "insight": "Ya no aplica."}
```"""
        assert agent._apply_reflection(response)["removed"] == 1
        assert agent.memory.lessons == []


class TestState:
    def test_state_shape(self, agent):
        state = agent.get_state()
        for key in ("enabled", "has_brain", "summary", "strategy_stats",
                    "lessons", "recent_verdicts", "reflect_due_in"):
            assert key in state
        assert json.dumps(state)  # must be JSON-serialisable for the API
