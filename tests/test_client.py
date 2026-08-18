"""Tests for the Polymarket HTTP client's rate-limit defences.

Polymarket sits behind Cloudflare and resets connections from clients that
look like scrapers or exceed its limits, so these pin the behaviours that
keep us under the wire: identification, batching, caching and backoff.
"""
import time
from unittest.mock import Mock, patch

import pytest
import requests

from config import Config
from src.api.polymarket_client import USER_AGENT, PolymarketClient


def json_response(payload, status=200):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = Mock()
    return resp


@pytest.fixture
def client():
    # No pacing delay in tests
    return PolymarketClient(Config(), min_interval=0)


class TestIdentification:
    def test_sends_a_real_user_agent(self, client):
        agent = client.session.headers["User-Agent"]
        assert "python-requests" not in agent
        assert agent == USER_AGENT

    def test_sends_browser_like_headers(self, client):
        for header in ("Accept", "Origin", "Referer"):
            assert header in client.session.headers


class TestBatching:
    def test_one_request_for_many_tokens(self, client):
        with patch.object(client.session, "request") as request:
            request.return_value = json_response(
                {"tok1": {"mid": "0.42"}, "tok2": {"mid": "0.58"}})
            prices = client.get_midpoints(["tok1", "tok2"])

        assert request.call_count == 1
        assert prices == {"tok1": 0.42, "tok2": 0.58}

    def test_accepts_list_shaped_response(self, client):
        with patch.object(client.session, "request") as request:
            request.return_value = json_response(
                [{"token_id": "tok1", "mid": "0.31"}])
            assert client.get_midpoints(["tok1"]) == {"tok1": 0.31}

    def test_empty_input_makes_no_request(self, client):
        with patch.object(client.session, "request") as request:
            assert client.get_midpoints([]) == {}
            request.assert_not_called()

    def test_falls_back_when_batch_endpoint_fails(self, client):
        calls = []

        def fake(method, url, **kwargs):
            calls.append(url)
            if url.endswith("/midpoints"):
                raise requests.ConnectionError("reset")
            return json_response({"mid": "0.44"})

        # Skip the real backoff wait — it is exercised in TestBackoff
        with patch("src.api.polymarket_client.time.sleep"), \
             patch.object(client.session, "request", side_effect=fake):
            prices = client.get_midpoints(["tok1"])

        assert prices == {"tok1": 0.44}
        assert any(u.endswith("/midpoint") for u in calls)

    def test_falls_back_when_batch_returns_nothing_usable(self, client):
        def fake(method, url, **kwargs):
            if url.endswith("/midpoints"):
                return json_response({})
            return json_response({"mid": "0.5"})

        with patch.object(client.session, "request", side_effect=fake):
            assert client.get_midpoints(["tok1"]) == {"tok1": 0.5}


class TestHistoryCache:
    def test_second_call_is_served_from_cache(self, client):
        with patch.object(client.session, "request") as request:
            request.return_value = json_response({"history": [{"p": 0.4}]})
            first = client.get_prices_history("tok1")
            second = client.get_prices_history("tok1")

        assert request.call_count == 1
        assert first == second == [{"p": 0.4}]

    def test_different_tokens_are_cached_separately(self, client):
        with patch.object(client.session, "request") as request:
            request.return_value = json_response({"history": [{"p": 0.4}]})
            client.get_prices_history("tok1")
            client.get_prices_history("tok2")
        assert request.call_count == 2

    def test_expired_cache_refetches(self, client):
        client._history_ttl = 0
        with patch.object(client.session, "request") as request:
            request.return_value = json_response({"history": [{"p": 0.4}]})
            client.get_prices_history("tok1")
            client.get_prices_history("tok1")
        assert request.call_count == 2

    def test_serves_stale_data_when_the_api_fails(self, client):
        with patch.object(client.session, "request") as request:
            request.return_value = json_response({"history": [{"p": 0.4}]})
            client.get_prices_history("tok1")

        client._history_ttl = 0
        with patch("src.api.polymarket_client.time.sleep"), \
             patch.object(client.session, "request",
                          side_effect=requests.ConnectionError("reset")):
            # Better a slightly old candle than losing the signal entirely
            assert client.get_prices_history("tok1") == [{"p": 0.4}]


class TestBackoff:
    def test_throttled_status_waits_longer_than_a_plain_error(self, client):
        waits = []
        with patch("src.api.polymarket_client.time.sleep", waits.append), \
             patch.object(client.session, "request") as request:
            request.return_value = json_response({}, status=429)
            with pytest.raises(requests.HTTPError):
                client._get("https://example.test/x")

        assert waits, "a throttled response must back off"
        assert min(waits) >= 5

    def test_connection_reset_backs_off_hard(self, client):
        waits = []
        with patch("src.api.polymarket_client.time.sleep", waits.append), \
             patch.object(client.session, "request",
                          side_effect=requests.ConnectionError("reset by peer")):
            with pytest.raises(requests.ConnectionError):
                client._get("https://example.test/x")

        assert waits and min(waits) >= 5

    def test_gives_up_and_reraises(self, client):
        with patch("src.api.polymarket_client.time.sleep"), \
             patch.object(client.session, "request",
                          side_effect=requests.ConnectionError("reset")) as request:
            with pytest.raises(requests.ConnectionError):
                client._get("https://example.test/x", retries=3)
        assert request.call_count == 3


class TestThrottle:
    def test_paces_consecutive_requests(self):
        paced = PolymarketClient(Config(), min_interval=0.05)
        with patch.object(paced.session, "request") as request:
            request.return_value = json_response({})
            started = time.monotonic()
            for _ in range(3):
                paced._get("https://example.test/x")
            elapsed = time.monotonic() - started

        # Three requests at 50ms apart cannot finish instantly
        assert elapsed >= 0.09
