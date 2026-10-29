"""c1_gateway: 방(세션)·플레이어·QR 발급, 폰↔그림판 WebSocket 중계.

한 방에 여러 플레이어가 함께 그린다. 플레이어 = 자기 그림판(canvas) 1개 + 폰(cam) 1개.

흐름:
  canvas  --POST /api/session-->                방 생성 (방장만)
  canvas  --POST /api/session/{sid}/players-->  플레이어 입장 + 자기 폰용 QR(SVG)
  canvas  --WS /ws/canvas/{sid}/{pid}-->        방의 모든 플레이어 제스처 수신, 펜/지우기/스냅샷 송신
  cam     --WS /ws/cam/{sid}/{pid}----->        랜드마크 송신 → c2_gesture 로 전달
  c2_gesture 가 돌려준 GestureEvent 에 player 를 붙여 → 방의 모든 canvas 로 전달
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
from pydantic import TypeAdapter, ValidationError
from websockets.asyncio.client import connect as ws_connect

from common.schema import (
    CanvasMessage, GestureEvent, PlayerInfo, PlayersEvent, PlayerState, SessionInfo, Snapshot, SnapshotRequest,
)

C2_URL = os.getenv("C2_URL", "ws://c2_gesture:8001")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "").strip()  # QR에 넣을 PC의 LAN IP(또는 도메인)
HTTPS_PORT = os.getenv("HTTPS_PORT", "443").strip()
SESSION_TTL = int(os.getenv("SESSION_TTL", "3600"))
PLAYER_IDLE_SEC = int(os.getenv("PLAYER_IDLE_SEC", "300"))  # 연결이 모두 끊긴 플레이어 자리 유지 시간(새로고침 대비)

# 플레이어별 손·커서 색. 최대 인원 = 색 개수
PLAYER_COLORS = ["#e5484d", "#4f8cff", "#30a46c", "#f5a623", "#8e4ec6", "#e93d82"]

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)

canvas_message = TypeAdapter(CanvasMessage)


@dataclass
class Player:
    id: str
    color: str
    canvas: WebSocket | None = None
    cam: WebSocket | None = None
    last_seen: float = field(default_factory=time.time)


@dataclass
class Session:
    id: str
    created: float = field(default_factory=time.time)
    players: dict[str, Player] = field(default_factory=dict)

    def connected(self) -> bool:
        return any(p.canvas or p.cam for p in self.players.values())


sessions: dict[str, Session] = {}


async def _send(ws: WebSocket | None, text: str):
    if ws is None:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(text)


async def _broadcast(s: Session, text: str):
    for p in list(s.players.values()):
        await _send(p.canvas, text)


async def _broadcast_players(s: Session):
    ev = PlayersEvent(players=[
        PlayerState(id=p.id, color=p.color, cam=p.cam is not None, canvas=p.canvas is not None)
        for p in s.players.values()
    ])
    await _broadcast(s, ev.model_dump_json())


async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for sid, s in list(sessions.items()):
            idle = [pid for pid, p in s.players.items()
                    if not p.canvas and not p.cam and now - p.last_seen > PLAYER_IDLE_SEC]
            for pid in idle:
                s.players.pop(pid, None)
            if idle:
                await _broadcast_players(s)
            if not s.connected() and now - s.created > SESSION_TTL:
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


def _public_host(request: Request) -> str:
    """QR·초대 링크에 넣을 호스트."""
    if request.headers.get("x-via-tunnel"):
        return request.headers.get("host", "localhost")  # Cloudflare Tunnel 공개 주소 (443)
    if PUBLIC_HOST:
        return PUBLIC_HOST if HTTPS_PORT == "443" else f"{PUBLIC_HOST}:{HTTPS_PORT}"
    return request.headers.get("host", "localhost")  # edge 가 포트 포함 Host 를 전달


def _invite_url(host: str, sid: str) -> str:
    return f"https://{host}/canvas.html?room={sid}"


@app.get("/healthz")
async def healthz():
    return {"ok": True, "sessions": len(sessions)}


@app.post("/api/session", response_model=SessionInfo)
async def create_session(request: Request):
    sid = secrets.token_urlsafe(8)
    sessions[sid] = Session(id=sid)
    return SessionInfo(session_id=sid, invite_url=_invite_url(_public_host(request), sid))


@app.post("/api/session/{sid}/players", response_model=PlayerInfo)
async def join_session(sid: str, request: Request, player: str | None = None):
    """방에 입장. player 로 기존 ID 를 주면(새로고침) 같은 자리·색·QR 을 돌려준다."""
    s = sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    p = s.players.get(player) if player else None
    if p is None:
        used = {q.color for q in s.players.values()}
        free = [c for c in PLAYER_COLORS if c not in used]
        if not free:
            raise HTTPException(409, "room is full")
        p = Player(id=secrets.token_urlsafe(6), color=free[0])
        s.players[p.id] = p
    p.last_seen = time.time()
    host = _public_host(request)
    cam_url = f"https://{host}/cam.html?s={sid}&p={p.id}&c={p.color.lstrip('#')}"
    return PlayerInfo(session_id=sid, player_id=p.id, color=p.color, cam_url=cam_url,
                      qr_svg=_qr_svg(cam_url), invite_url=_invite_url(host, sid))


@app.get("/api/session/{sid}")
async def get_session(sid: str):
    s = sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    return {"session_id": sid, "players": [
        {"id": p.id, "color": p.color, "cam": p.cam is not None, "canvas": p.canvas is not None}
        for p in s.players.values()
    ]}


async def _accept(ws: WebSocket, sid: str, pid: str) -> tuple[Session, Player] | None:
    await ws.accept()
    s = sessions.get(sid)
    p = s.players.get(pid) if s else None
    if not p:
        await ws.close(code=4404)  # accept 후 close 해야 브라우저가 코드를 받음
        return None
    return s, p


@app.websocket("/ws/canvas/{sid}/{pid}")
async def canvas_ws(ws: WebSocket, sid: str, pid: str):
    if not (found := await _accept(ws, sid, pid)):
        return
    s, p = found
    if p.canvas:
        with contextlib.suppress(Exception):
            await p.canvas.close(code=4409)
    p.canvas = ws
    await _broadcast_players(s)
    # 이미 그리고 있던 다른 그림판에서 현재 그림을 받아 오도록 요청
    source = next((q.canvas for q in s.players.values() if q.canvas and q is not p), None)
    await _send(source, SnapshotRequest(to=pid).model_dump_json())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = canvas_message.validate_json(raw)
            except ValidationError:
                continue  # ping 등
            if isinstance(msg, Snapshot):
                target = s.players.get(msg.to)
                await _send(target.canvas if target else None, msg.model_dump_json())
            else:  # 펜 변경, 전체 지우기 → 보낸 그림판 포함 모두에게 (같은 순서로 적용되도록)
                msg.player = pid
                await _broadcast(s, msg.model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        if p.canvas is ws:
            p.canvas = None
            p.last_seen = time.time()
            await _broadcast_players(s)


@app.websocket("/ws/cam/{sid}/{pid}")
async def cam_ws(ws: WebSocket, sid: str, pid: str):
    if not (found := await _accept(ws, sid, pid)):
        return
    s, p = found
    if p.cam:
        with contextlib.suppress(Exception):
            await p.cam.close(code=4409)
    p.cam = ws
    await _broadcast_players(s)
    log.info("cam joined: %s/%s", sid, pid)

    try:
        async with ws_connect(f"{C2_URL}/ws/{sid}-{pid}") as c2:

            async def upstream():  # cam → c2
                while True:
                    await c2.send(await ws.receive_text())

            async def downstream():  # c2 → 방의 모든 canvas (누구 손인지 표시)
                async for msg in c2:
                    ev = GestureEvent.model_validate_json(msg)
                    ev.player = pid
                    await _broadcast(s, ev.model_dump_json())

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
        if p.cam is ws:
            p.cam = None
            p.last_seen = time.time()
            await _broadcast_players(s)
        log.info("cam left: %s/%s", sid, pid)
