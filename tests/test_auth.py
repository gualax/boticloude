"""Tests for dashboard authentication."""
import pytest

import src.web as web
from config import Config


@pytest.fixture
def secured_client(monkeypatch):
    """A dashboard with a password set."""
    config = Config()
    config.DASHBOARD_USER = "admin"
    config.DASHBOARD_PASSWORD = "s3cret"
    monkeypatch.setattr(web, "_config", config)
    web.app.config["TESTING"] = True
    web.app.secret_key = "test-key"
    with web.app.test_client() as client:
        yield client


@pytest.fixture
def open_client(monkeypatch):
    """A dashboard with no password (local dev mode)."""
    config = Config()
    config.DASHBOARD_PASSWORD = ""
    monkeypatch.setattr(web, "_config", config)
    web.app.config["TESTING"] = True
    web.app.secret_key = "test-key"
    with web.app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def clear_lockouts():
    from src import auth
    auth._failed_attempts.clear()
    yield
    auth._failed_attempts.clear()


class TestProtectedDashboard:
    def test_dashboard_redirects_when_anonymous(self, secured_client):
        response = secured_client.get("/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_api_returns_401_when_anonymous(self, secured_client):
        response = secured_client.get("/api/positions")
        assert response.status_code == 401

    def test_login_page_renders(self, secured_client):
        response = secured_client.get("/login")
        assert response.status_code == 200
        assert b"Acceso al panel" in response.data

    def test_wrong_password_is_rejected(self, secured_client):
        response = secured_client.post(
            "/login", data={"username": "admin", "password": "wrong"})
        assert response.status_code == 401

    def test_wrong_username_is_rejected(self, secured_client):
        response = secured_client.post(
            "/login", data={"username": "hacker", "password": "s3cret"})
        assert response.status_code == 401

    def test_correct_credentials_grant_access(self, secured_client):
        response = secured_client.post(
            "/login", data={"username": "admin", "password": "s3cret"})
        assert response.status_code == 302
        # The session now unlocks the API
        assert secured_client.get("/api/positions").status_code == 200

    def test_logout_revokes_access(self, secured_client):
        secured_client.post("/login",
                            data={"username": "admin", "password": "s3cret"})
        assert secured_client.get("/api/positions").status_code == 200
        secured_client.get("/logout")
        assert secured_client.get("/api/positions").status_code == 401

    def test_lockout_after_repeated_failures(self, secured_client):
        for _ in range(5):
            secured_client.post(
                "/login", data={"username": "admin", "password": "wrong"})
        # Even the right password is refused while locked out
        response = secured_client.post(
            "/login", data={"username": "admin", "password": "s3cret"})
        assert response.status_code == 429


class TestOpenDashboard:
    def test_dashboard_is_reachable_without_login(self, open_client):
        assert open_client.get("/").status_code == 200

    def test_api_is_reachable_without_login(self, open_client):
        assert open_client.get("/api/positions").status_code == 200

    def test_login_page_redirects_home(self, open_client):
        response = open_client.get("/login")
        assert response.status_code == 302
