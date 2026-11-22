"""Diffusion temps réel (WebSocket) : l'application reçoit mesures et alertes sans interroger le serveur (F6.7)."""

import asyncio
import contextlib
import logging

import anyio.from_thread
from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: dict) -> None:
        if not self._clients:
            return
        results = await asyncio.gather(*(self._send(ws, event) for ws in list(self._clients)))
        for ws, ok in zip(list(self._clients), results, strict=False):
            if not ok:
                self.disconnect(ws)

    @staticmethod
    async def _send(ws: WebSocket, event: dict) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(event), timeout=3)
            return True
        except Exception:
            return False


hub = Hub()


def publish(event: dict) -> None:
    """À appeler depuis du code synchrone exécuté dans un thread du serveur (endpoint sync, tâche de fond)."""
    with contextlib.suppress(RuntimeError):  # hors thread AnyIO (scripts, tests unitaires) : pas de clients WS
        anyio.from_thread.run(hub.broadcast, event)


def alert_event(alert, event_type: str) -> dict:
    return {
        "type": event_type,  # alert_created | alert_updated | alert_resolved
        "alert": {
            "id": alert.id,
            "point_id": alert.point_id,
            "level": alert.level,
            "status": alert.status,
            "reason": alert.reason,
            "message_residents": alert.message_residents,
            "message_source": alert.message_source,
            "created_at": alert.created_at.isoformat(),
        },
    }


def measurement_event(point, measurement) -> dict:
    return {
        "type": "measurement",
        "point_id": point.id,
        "point_code": point.code,
        "level": measurement.alert_level,
        "water_height_cm": measurement.water_height_cm,
        "water_level_pct": measurement.water_level_pct,
        "rise_rate_cm_min": measurement.rise_rate_cm_min,
        "obstruction_pct": measurement.obstruction_pct,
        "obstruction_class": measurement.obstruction_class,
        "blockage_detected": measurement.blockage_detected,
        "measured_at": measurement.measured_at.isoformat(),
    }
