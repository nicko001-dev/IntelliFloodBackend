from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime import hub

router = APIRouter(tags=["Temps réel"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Flux d'événements : {type: measurement | alert_created | alert_updated, ...}. Le client peut envoyer « ping »."""
    await hub.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
