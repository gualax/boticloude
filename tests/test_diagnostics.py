"""Tests for the trading-funnel diagnostic."""
from dataclasses import dataclass

import pytest

from config import Config
from src.analysis.market_analyzer import MarketSignal
from src.diagnostics import diagnose


def signal(**kwargs):
    defaults = dict(
        token_id="t1", market_name="Test market", condition_id="c1",
        side="Yes", current_price=0.50, fair_value=0.62, edge=0.12,
        confidence=0.6, strategy="mispricing", liquidity=1000.0,
    )
    defaults.update(kwargs)
    return MarketSignal(**defaults)


class FakeClient:
    def __init__(self, markets=None, fail=False):
        self._markets = markets if markets is not None else [{}] * 5
        self._fail = fail

    def get_markets(self, **kwargs):
        if self._fail:
            raise ConnectionError("proxy denied")
        return self._markets


class FakeAnalyzer:
    def __init__(self, markets, signals):
        self._markets = markets
        self._signals = signals

    def scan_markets(self, limit=100):
        return self._markets

    def generate_signals(self, markets=None):
        return self._signals


class FakeBot:
    def __init__(self, raw=5, markets=None, signals=None, config=None):
        self.config = config or Config()
        self.client = FakeClient([{}] * raw)
        if markets is None:
            markets = [{"question": f"M{i}", "liquidity": 900.0,
                        "volume": 5000.0} for i in range(3)]
        self.analyzer = FakeAnalyzer(markets, signals or [])
        from src.agents.hermes import HermesAgent
        from src.risk.risk_manager import RiskManager
        self.risk = RiskManager(self.config)
        self.config.GEMINI_API_KEY = ""
        self.hermes = HermesAgent(self.config)
        self.paper = _FakePaper()


class _FakePaper:
    balance = 1000.0
    positions: dict = {}


@pytest.fixture(autouse=True)
def isolated_hermes(tmp_path, monkeypatch):
    from src.agents import hermes_memory
    monkeypatch.setattr(hermes_memory, "HERMES_DIR", str(tmp_path))
    monkeypatch.setattr(hermes_memory, "MEMORY_FILE",
                        str(tmp_path / "memory.json"))


class TestDiagnose:
    def test_reports_connection_failure(self, capsys):
        bot = FakeBot()
        bot.client = FakeClient(fail=True)
        result = diagnose(bot)
        assert "error" in result
        assert "NO SE PUDO CONECTAR" in capsys.readouterr().out

    def test_reports_no_markets(self, capsys):
        bot = FakeBot(raw=0)
        bot.client = FakeClient([])
        result = diagnose(bot)
        assert result["markets_raw"] == 0
        assert result["tradeable"] == 0

    def test_result_shape_is_stable_at_every_stage(self):
        """Callers must get the same keys no matter where the walk ends."""
        expected = {"error", "markets_raw", "markets_suitable", "signals",
                    "passed_risk", "approved_hermes", "tradeable"}
        cases = [
            FakeBot(raw=0),                      # stops at connection
            FakeBot(markets=[]),                 # stops at market filter
            FakeBot(signals=[]),                 # stops at signal generation
            FakeBot(signals=[signal(edge=0.001)]),  # stops at risk filter
            FakeBot(signals=[signal()]),         # walks the whole funnel
        ]
        cases[0].client = FakeClient([])
        for bot in cases:
            assert set(diagnose(bot).keys()) == expected

    def test_reports_zero_signals(self, capsys):
        bot = FakeBot(signals=[])
        result = diagnose(bot)
        assert result["signals"] == 0
        assert "Ninguna estrategia encontro oportunidad" in capsys.readouterr().out

    def test_counts_a_clean_funnel(self, capsys):
        bot = FakeBot(signals=[signal(), signal(token_id="t2")])
        result = diagnose(bot)
        assert result["signals"] == 2
        assert result["passed_risk"] == 2
        assert result["approved_hermes"] == 2
        assert result["tradeable"] == 2
        assert "El bot compraria" in capsys.readouterr().out

    def test_explains_low_edge_rejection(self, capsys):
        bot = FakeBot(signals=[signal(edge=0.005)])
        result = diagnose(bot)
        assert result["passed_risk"] == 0
        assert "edge" in capsys.readouterr().out

    def test_explains_low_liquidity_rejection(self, capsys):
        bot = FakeBot(signals=[signal(liquidity=10.0)])
        result = diagnose(bot)
        assert result["passed_risk"] == 0
        assert "liquidez" in capsys.readouterr().out

    def test_explains_penny_token_rejection(self, capsys):
        bot = FakeBot(signals=[signal(current_price=0.01)])
        result = diagnose(bot)
        assert result["passed_risk"] == 0
        assert "precio" in capsys.readouterr().out

    def test_shows_hermes_veto(self, capsys):
        from src.agents.hermes_memory import Lesson
        bot = FakeBot(signals=[signal(strategy="momentum")])
        bot.hermes.memory.add_lesson(Lesson(
            id="L1", text="Evitar momentum", when={"strategy": "momentum"},
            action="avoid", confidence=0.9, evidence_count=8))
        result = diagnose(bot)
        assert result["passed_risk"] == 1
        assert result["approved_hermes"] == 0
        assert "L1" in capsys.readouterr().out


class TestRejectionReasons:
    def test_passing_signal_has_no_reason(self):
        from src.risk.risk_manager import RiskManager
        assert RiskManager(Config()).rejection_reason(signal()) is None

    def test_each_filter_names_itself(self):
        from src.risk.risk_manager import RiskManager
        rm = RiskManager(Config())
        assert "edge" in rm.rejection_reason(signal(edge=0.001))
        assert "liquidez" in rm.rejection_reason(signal(liquidity=1))
        assert "confianza" in rm.rejection_reason(signal(confidence=0.01))
        assert "precio" in rm.rejection_reason(signal(current_price=0.01))
        assert "precio" in rm.rejection_reason(signal(current_price=0.99))

    def test_validate_signal_agrees_with_reason(self):
        from src.risk.risk_manager import RiskManager
        rm = RiskManager(Config())
        for candidate in (signal(), signal(edge=0.001), signal(liquidity=1)):
            assert rm.validate_signal(candidate) == (
                rm.rejection_reason(candidate) is None)
