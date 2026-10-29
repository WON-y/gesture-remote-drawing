"""통합 테스트: docker compose 로 띄운 전체 서비스(edge → c1_gateway → c2_gesture)를 실제로 통과시킨다.

  방 생성 → 플레이어 입장 → 그림판 WS 연결 → 폰 WS 연결 → 랜드마크 전송 → 그림판에 draw 제스처 도착 → 폰 종료 알림

사용법: python scripts/smoke_test.py [https://localhost]
자체 서명 인증서를 쓰므로 인증서 검증은 끈다.
"""
import asyncio
import json
import ssl
import sys
import urllib.request

from websockets.asyncio.client import connect

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://localhost").rstrip("/")
WS_BASE = BASE.replace("https://", "wss://", 1)
TIMEOUT = 10

SSL = ssl.create_default_context()
SSL.check_hostname = False
SSL.verify_mode = ssl.CERT_NONE


def draw_hand():
    """검지만 편 손 (21개 랜드마크)."""
    lm = [[0.5, 0.9, 0.0] for _ in range(21)]
    for k, (tip, pip) in enumerate([(8, 6), (12, 10), (16, 14), (20, 18)]):
        x = 0.4 + 0.07 * k
        lm[tip - 3], lm[pip] = [x, 0.7, 0.0], [x, 0.6, 0.0]
        lm[tip - 1], lm[tip] = ([x, 0.5, 0.0], [x, 0.4, 0.0]) if k == 0 else ([x, 0.68, 0.0], [x, 0.75, 0.0])
    for j in range(1, 5):
        lm[j] = [0.36, 0.85 - 0.02 * j, 0.0]
    return lm


async def recv_until(ws, pred, what):
    async def loop():
        while True:
            msg = json.loads(await ws.recv())
            if pred(msg):
                return msg
    try:
        return await asyncio.wait_for(loop(), TIMEOUT)
    except asyncio.TimeoutError:
        raise SystemExit(f"FAIL: {what} 을(를) {TIMEOUT}초 안에 받지 못함")


def post(path):
    req = urllib.request.Request(f"{BASE}{path}", method="POST")
    with urllib.request.urlopen(req, context=SSL, timeout=TIMEOUT) as r:
        return json.load(r)


def players_event(pid, cam):
    """방의 플레이어 목록 중 pid 의 폰 연결 상태가 cam 인 players 이벤트인지."""
    return lambda m: m.get("type") == "players" and any(p["id"] == pid and p["cam"] is cam for p in m["players"])


async def main():
    room = post("/api/session")
    sid = room["session_id"]
    assert room["invite_url"].endswith(f"/canvas.html?room={sid}"), room["invite_url"]
    print(f"ok  방 생성: {sid}")

    info = post(f"/api/session/{sid}/players")
    pid = info["player_id"]
    assert f"/cam.html?s={sid}&p={pid}&c=" in info["cam_url"], info["cam_url"]
    assert info["qr_svg"].startswith("<svg"), "QR 이 SVG 가 아님"
    print(f"ok  플레이어 입장: {pid} ({info['color']})")

    async with connect(f"{WS_BASE}/ws/canvas/{sid}/{pid}", ssl=SSL) as canvas:
        await recv_until(canvas, players_event(pid, False), "초기 players(폰 없음)")
        print("ok  그림판 연결")

        async with connect(f"{WS_BASE}/ws/cam/{sid}/{pid}", ssl=SSL) as cam:
            await recv_until(canvas, players_event(pid, True), "players(폰 연결)")
            print("ok  폰 연결 알림")

            lm = draw_hand()
            for i in range(15):
                await cam.send(json.dumps({"type": "landmarks", "ts": i * 33.0, "mirrored": True, "landmarks": lm}))
            ev = await recv_until(canvas, lambda m: m.get("type") == "gesture" and m["gesture"] == "draw",
                                  "draw 제스처")
            assert 0 <= ev["x"] <= 1 and 0 <= ev["y"] <= 1, ev
            assert ev["player"] == pid, ev
            print(f"ok  랜드마크 → draw 제스처 도착 (x={ev['x']}, y={ev['y']})")

        await recv_until(canvas, players_event(pid, False), "players(폰 종료)")
        print("ok  폰 종료 알림")

    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
