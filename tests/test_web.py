"""Tests for the web dashboard API endpoints."""
import json
import pytest

from src.web import app, _bot_log, _last_signals, _cycle_history


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestWebAPI:
    def test_index_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"Polymarket Bot" in r.data

    def test_summary_no_bot(self, client):
        r = client.get("/api/summary")
        assert r.status_code == 503

    def test_positions_empty(self, client):
        r = client.get("/api/positions")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_trades_empty(self, client):
        r = client.get("/api/trades")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_signals_empty(self, client):
        r = client.get("/api/signals")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_history_empty(self, client):
        r = client.get("/api/history")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_logs_empty(self, client):
        r = client.get("/api/logs")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)

    def test_bot_start_stop(self, client):
        r = client.post("/api/bot/start")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["status"] == "started"

        r = client.post("/api/bot/stop")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["status"] == "stopped"
