"""Tests for the web dashboard API endpoints.

These cover the shape of the API, not the login gate — that lives in
test_auth.py — so the fixture logs in before handing back a client.
"""
import json
import pytest

import src.web as web
from config import Config
from src.web import app, _bot_log, _last_signals, _cycle_history


@pytest.fixture
def client(monkeypatch):
    config = Config()
    config.DASHBOARD_USER = "admin"
    config.DASHBOARD_PASSWORD = "test-password"
    monkeypatch.setattr(web, "_config", config)
    app.config["TESTING"] = True
    app.secret_key = "test-key"
    with app.test_client() as c:
        c.post("/login", data={"username": "admin",
                               "password": "test-password"})
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
