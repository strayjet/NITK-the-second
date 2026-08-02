"""
WebSocket routes: the real-time push side of citymind-backend.

`/ws/live` streams every incident/dashboard event as it happens, as JSON
frames of the shape `{"type": "<event.type>", "data": {...}}`. See
`app.services.realtime.RealtimeHub` for how events get here.

Clients don't need to send anything after connecting — this is a
server-push-only channel — but the endpoint tolerates and ignores any
incoming text (e.g. WebSocket ping frames some clients send as text) so it
doesn't error out on a chatty client.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime import RealtimeHub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    hub: RealtimeHub = websocket.app.state.realtime_hub
    await hub.connect(websocket)
    try:
        await websocket.send_json({"type": "connected", "data": {"message": "citymind realtime channel open"}})
        while True:
            # Server-push channel: we don't expect input, but draining
            # incoming frames keeps the connection alive and lets us detect
            # disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - a single bad client must not affect the server
        logger.debug("WebSocket /ws/live closed with error: %s", exc)
    finally:
        await hub.disconnect(websocket)
