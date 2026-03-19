"""WebSocket client for real-time Polymarket market data."""
import asyncio
import json
import logging
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketWebSocket:
    """Real-time market data via Polymarket CLOB WebSocket."""

    def __init__(self):
        self._ws = None
        self._running = False
        self._callbacks: dict[str, list[Callable]] = {
            "book": [],
            "price_change": [],
            "last_trade_price": [],
            "best_bid_ask": [],
            "market_resolved": [],
        }

    def on(self, event: str, callback: Callable):
        """Register a callback for a specific event type."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    async def subscribe(self, token_ids: list[str]):
        """Subscribe to market data for given token IDs."""
        if not self._ws:
            return
        msg = {
            "assets_ids": token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await self._ws.send(json.dumps(msg))
        logger.info("Subscribed to %d tokens via WebSocket", len(token_ids))

    async def connect(self, token_ids: list[str],
                      reconnect: bool = True):
        """Connect to the WebSocket and start receiving data."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(WS_URL) as ws:
                    self._ws = ws
                    logger.info("WebSocket connected to %s", WS_URL)
                    await self.subscribe(token_ids)

                    async for raw_msg in ws:
                        try:
                            data = json.loads(raw_msg)
                            await self._dispatch(data)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from WebSocket")

            except websockets.ConnectionClosed as e:
                logger.warning("WebSocket disconnected: %s", e)
                if not reconnect:
                    break
                logger.info("Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("WebSocket error: %s", e)
                if not reconnect:
                    break
                await asyncio.sleep(5)

        self._ws = None

    async def _dispatch(self, data: dict | list):
        """Route incoming messages to registered callbacks."""
        events = data if isinstance(data, list) else [data]
        for event in events:
            event_type = event.get("event_type", "")
            if event_type in self._callbacks:
                for cb in self._callbacks[event_type]:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(event)
                        else:
                            cb(event)
                    except Exception as e:
                        logger.error("Callback error for %s: %s",
                                     event_type, e)

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
