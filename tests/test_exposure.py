"""Tests for the guard that stops the panel being served unauthenticated.

The dashboard starts and stops the bot and shows the whole portfolio, so
binding it to a public interface without a password hands control to
anyone who port-scans the host. These pin that it cannot happen by
accident.
"""
from pathlib import Path

import pytest

from config import Config
from src.web import (InsecureExposureError, check_exposure_is_safe,
                     require_password_for_production)


def config_with(password: str) -> Config:
    config = Config()
    config.DASHBOARD_PASSWORD = password
    return config


class TestPublicBind:
    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "::"])
    def test_refuses_public_bind_without_password(self, host):
        with pytest.raises(InsecureExposureError) as excinfo:
            check_exposure_is_safe(config_with(""), host)
        message = str(excinfo.value)
        # The refusal has to explain itself and offer a way forward
        assert "DASHBOARD_PASSWORD" in message
        assert "127.0.0.1" in message

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50"])
    def test_allows_public_bind_with_password(self, host):
        check_exposure_is_safe(config_with("a-real-password"), host)


class TestLocalBind:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_local_bind_needs_no_password(self, host):
        # Only reachable from the machine itself, so a login adds nothing
        check_exposure_is_safe(config_with(""), host)

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
    def test_local_bind_accepts_a_password_too(self, host):
        check_exposure_is_safe(config_with("a-real-password"), host)


class TestProductionEntrypoint:
    """gunicorn always serves a network, so a password is never optional."""

    def test_refuses_without_a_password(self):
        with pytest.raises(InsecureExposureError, match="DASHBOARD_PASSWORD"):
            require_password_for_production(config_with(""))

    def test_accepts_with_a_password(self):
        require_password_for_production(config_with("a-real-password"))

    def test_wsgi_entrypoint_enforces_it(self):
        """The production entrypoint must actually call the guard."""
        source = (Path(__file__).parent.parent / "wsgi.py").read_text()
        assert "require_password_for_production" in source
