"""c2_gesture: c1_gateway 로부터 LandmarkFrame 을 받아 GestureEvent 를 돌려준다."""
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from common.schema import LandmarkFrame
from c2_gesture.gesture import GestureEngine

log = logging.getLogger("gesture")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="airsketch c2_gesture")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.websocket("/ws/{sid}")
async def gesture_ws(ws: WebSocket, sid: str):
    await ws.accept()
    engine = GestureEngine()
    log.info("stream opened: %s", sid)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = LandmarkFrame.model_validate_json(raw)
            except ValidationError:
                continue
            if event := engine.update(frame):
                await ws.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        pass
    log.info("stream closed: %s", sid)
