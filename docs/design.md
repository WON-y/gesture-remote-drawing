# AirSketch 설계

폰 카메라로 손을 인식해 PC 브라우저 그림판에 허공 제스처로 그림을 그린다.

## 구성

```
 [폰] cam.html                       [PC] canvas.html
  MediaPipe Hand Landmarker (JS)       그림판 + QR
        │ wss /ws/cam/{sid}                ▲ wss /ws/canvas/{sid}
        ▼                                  │
 ┌──────────────── edge (Nginx, HTTPS) ────────────────┐
 │  /            → web/ 정적 파일                       │
 │  /api, /ws    → c1_gateway:8000                     │
 └─────────────────────────────────────────────────────┘
        │
   c1_gateway (FastAPI)  ── ws /ws/{sid} ──▶  c2_gesture (FastAPI)
   세션·QR 발급, 중계     ◀── GestureEvent ──  제스처 판정 + 스무딩
```

| 폴더 | 역할 |
|---|---|
| `c1_gateway/` | `POST /api/session` 으로 세션 ID + QR(SVG) 발급, cam ↔ c2 ↔ canvas WebSocket 중계 |
| `c2_gesture/` | 21개 랜드마크 → 제스처 판정, One Euro Filter 로 좌표 스무딩, 디바운스 |
| `web/` | `cam.html`(폰, MediaPipe JS), `canvas.html`(PC, 그림판) |
| `edge/` | Nginx. TLS 종료 (폰은 HTTPS 에서만 카메라 허용), 정적 파일 + 프록시 |
| `common/schema.py` | 메시지 규격 (pydantic). 두 서비스 이미지에 함께 복사됨 |
| `scripts/make-cert.sh` | 자체 서명 인증서 생성 (LAN IP 를 SAN 에 포함) |

랜드마크 추출은 폰(브라우저)에서, 판정은 서버(c2)에서 한다. 영상이 아닌
좌표(21×3 floats)만 전송하므로 트래픽이 작다.

## 흐름

1. PC 에서 `https://<PC IP>:<포트>/` 접속 → `canvas.html` 이 세션 생성, QR 표시
2. 폰으로 QR 스캔 → `cam.html?s=<sid>` → 카메라 + Hand Landmarker 실행
3. 폰이 프레임마다 `LandmarkFrame` 전송 (최대 30fps, 손이 사라지면 빈 배열 1회)
4. gateway 가 c2 로 전달 → c2 가 `GestureEvent` 반환 → gateway 가 그림판으로 전달
5. 폰 연결/해제 시 그림판에 `PeerEvent` 전송 (연결되면 QR 숨김)

## 메시지 규격 (`common/schema.py`)

```jsonc
// cam → c2
{"type":"landmarks","ts":1234.5,"mirrored":true,"handedness":"Right",
 "landmarks":[[x,y,z], ... 21개]}          // 손 없음 = []
// c2 → canvas
{"type":"gesture","gesture":"draw","x":0.62,"y":0.37,"ts":1234.5}
// gateway → canvas
{"type":"peer","role":"cam","state":"joined"}
```

WebSocket 종료 코드: `4404` 없는 세션, `4409` 같은 역할의 새 연결로 교체됨.

## 제스처

| 손 모양 | 제스처 | 동작 |
|---|---|---|
| ☝ 검지만 | `draw` | 검지 끝(8)으로 선 그리기 |
| ✌ 검지+중지 | `hover` | 커서만 이동 |
| 🖐 네 손가락 | `erase` | 손바닥(9) 위치 지우개 |
| ✊ 주먹 | `idle` | 대기 |
| 손 없음 | `none` | 선 끊기 |

- 손가락 펴짐: `|tip−wrist| > |pip−wrist| × EXTEND_RATIO` (손 회전에 강함)
- 디바운스: 같은 제스처가 `STABLE_FRAMES` 연속일 때만 전환
- 좌표: 전면 카메라 좌우 반전 → 가장자리 `ACTIVE_MARGIN` 잘라 0~1 로 확장 → One Euro Filter

## 실행

```bash
cp .env.example .env               # PUBLIC_HOST 에 PC 의 LAN IP 입력
bash scripts/make-cert.sh          # edge/certs/ 에 인증서 생성
docker compose up -d --build
```

- PC: `https://<PUBLIC_HOST>:<HTTPS_PORT>/` 접속 (자체 서명 경고 → 계속 진행)
- 폰: 같은 Wi-Fi 에서 QR 스캔 → 인증서 경고 → 계속 진행 → "카메라 시작"
- 폰은 MediaPipe wasm/모델을 CDN 에서 받으므로 인터넷이 필요하다.
- 443/80 이 사용 중이면 `.env` 에서 `HTTPS_PORT`/`HTTP_PORT` 변경.
- 폰에서 접속이 안 되면 Windows 방화벽의 해당 포트 인바운드 허용을 확인.
