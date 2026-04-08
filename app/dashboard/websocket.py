"""Dashboard WebSocket server — broadcasts countdown, status, and events."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("nova7.dashboard.ws")


class DashboardWebSocket:
    """Manages WebSocket connections and broadcasts dashboard updates."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket, *, already_accepted: bool = False) -> None:
        if not already_accepted:
            await websocket.accept()
        self._connections.append(websocket)
        logger.info("Dashboard client connected (%d total)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("Dashboard client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_status(self, chaos_controller, service_manager) -> None:
        """Send a full status update to all connected clients."""
        msg = {
            "type": "status_update",
            "services": service_manager.get_all_status() if service_manager else {},
            "chaos": chaos_controller.get_status() if chaos_controller else {},
            "countdown": service_manager.get_countdown() if service_manager else {},
        }
        await self.broadcast(msg)

    async def broadcast_countdown(self, countdown_data: dict[str, Any]) -> None:
        await self.broadcast({"type": "countdown", **countdown_data})

    async def broadcast_event(self, event: dict[str, Any]) -> None:
        await self.broadcast({"type": "event", **event})
