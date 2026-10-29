"""c1_gateway: 세션·QR 발급, 폰↔그림판 WebSocket 중계.

흐름:
  canvas  --POST /api/session-->  세션 생성 + QR(SVG)
  canvas  --WS /ws/canvas/{sid}-->  제스처 이벤트 수신
  cam     --WS /ws/cam/{sid}----->  랜드마크 송신 → c2_gesture 로 전달
  c2_gesture 가 돌려준 GestureEvent → canvas 로 전달
"""
import asyncio
import contextlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect

from common.schema import PeerEvent, SessionInfo

C2_URL = os.getenv("C2_URL", "ws://c2_gesture:8001")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "").strip()  # QR에 넣을 PC의 LAN IP(또는 도메인)
HTTPS_PORT = os.getenv("HTTPS_PORT", "443").strip()
SESSION_TTL = int(os.getenv("SESSION_TTL", "3600"))

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)


@dataclass
class Session:
    id: str
    created: float = field(default_factory=time.time)
    canvas: WebSocket | None = None
    cam: WebSocket | None = None


sessions: dict[str, Session] = {}


async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for sid, s in list(sessions.items()):
            if not s.canvas and not s.cam and now - s.created > SESSION_TTL:
                sessions.pop(sid, None)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="airsketch c1_gateway", lifespan=lifespan)


def _qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


async def _send(ws: WebSocket | None, text: str):
    if ws is None:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(text)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "sessions": len(sessions)}


@app.post("/api/session", response_model=SessionInfo)
async def create_session(request: Request):
    sid = secrets.token_urlsafe(8)
    sessions[sid] = Session(id=sid)
    if PUBLIC_HOST:
        host = PUBLIC_HOST if HTTPS_PORT == "443" else f"{PUBLIC_HOST}:{HTTPS_PORT}"
    else:
        host = request.headers.get("host", "localhost")  # edge 가 포트 포함 Host 를 전달
    cam_url = f"https://{host}/cam.html?s={sid}"
    return SessionInfo(session_id=sid, cam_url=cam_url, qr_svg=_qr_svg(cam_url))


@app.get("/api/session/{sid}")
async def get_session(sid: str):
    s = sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    return {"session_id": sid, "cam": s.cam is not None, "canvas": s.canvas is not None}


@app.websocket("/ws/canvas/{sid}")
async def canvas_ws(ws: WebSocket, sid: str):
    s = sessions.get(sid)
    await ws.accept()
    if not s:
        await ws.close(code=4404)  # accept 후 close 해야 브라우저가 코드를 받음
        return
    if s.canvas:
        with contextlib.suppress(Exception):
            await s.canvas.close(code=4409)
    s.canvas = ws
    await _send(ws, PeerEvent(role="cam", state="joined" if s.cam else "left").model_dump_json())
    try:
        while True:
            await ws.receive_text()  # 그림판 → 서버 메시지는 현재 사용 안 함(ping 등)
    except WebSocketDisconnect:
        pass
    finally:
        if s.canvas is ws:
            s.canvas = None


@app.websocket("/ws/cam/{sid}")
async def cam_ws(ws: WebSocket, sid: str):
    s = sessions.get(sid)
    await ws.accept()
    if not s:
        await ws.close(code=4404)
        return
    if s.cam:
        with contextlib.suppress(Exception):
            await s.cam.close(code=4409)
    s.cam = ws
    await _send(s.canvas, PeerEvent(role="cam", state="joined").model_dump_json())
    log.info("cam joined: %s", sid)

    try:
        async with ws_connect(f"{C2_URL}/ws/{sid}") as c2:

            async def upstream():  # cam → c2
                while True:
                    await c2.send(await ws.receive_text())

            async def downstream():  # c2 → canvas
                async for msg in c2:
                    await _send(s.canvas, msg)

            tasks = {asyncio.create_task(upstream()), asyncio.create_task(downstream())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                if (e := t.exception()) and not isinstance(e, WebSocketDisconnect):
                    log.warning("relay ended: %r", e)
    except OSError as e:
        log.error("cannot reach c2_gesture: %r", e)
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
    finally:
        if s.cam is ws:
            s.cam = None
            await _send(s.canvas, PeerEvent(role="cam", state="left").model_dump_json())
        log.info("cam left: %s", sid)
