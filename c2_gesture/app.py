"""c2_gesture: c1_gateway 로부터 LandmarkFrame 을 받아 GestureEvent 를 돌려준다."""
import logging
import os
from collections import Counter

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from common.schema import LandmarkFrame
from c2_gesture.gesture import GestureEngine

log = logging.getLogger("gesture")
logging.basicConfig(level=logging.INFO)

STATS_SEC = float(os.getenv("STATS_SEC", "5"))  # 수신 통계 로그 주기(초), 0 이면 끔

app = FastAPI(title="airsketch c2_gesture")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.websocket("/ws/{sid}")
async def gesture_ws(ws: WebSocket, sid: str):
    await ws.accept()
    engine = GestureEngine()
    log.info("stream opened: %s", sid)
    # 수신 통계: 폰 기준 프레임 간격(ts)과 제스처 분포
    t0, prev_ts, max_gap, n, kinds = None, None, 0.0, 0, Counter()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = LandmarkFrame.model_validate_json(raw)
            except ValidationError:
                continue
            if event := engine.update(frame):
                await ws.send_text(event.model_dump_json())
                if STATS_SEC > 0 and frame.landmarks:
                    t0 = frame.ts if t0 is None else t0
                    if prev_ts is not None:
                        max_gap = max(max_gap, frame.ts - prev_ts)
                    prev_ts, n = frame.ts, n + 1
                    kinds[event.gesture.value] += 1
                    if frame.ts - t0 >= STATS_SEC * 1000:
                        log.info("stats %s: %.1f fps, max gap %.0f ms, %s",
                                 sid, n * 1000 / (frame.ts - t0), max_gap, dict(kinds))
                        t0, max_gap, n, kinds = frame.ts, 0.0, 0, Counter()
    except WebSocketDisconnect:
        pass
    log.info("stream closed: %s", sid)
