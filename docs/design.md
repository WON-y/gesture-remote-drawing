# AirSketch 설계

폰 카메라로 손을 인식해 PC 브라우저 그림판에 허공 제스처로 그림을 그린다.
한 방(세션)에 최대 6명이 각자 자기 PC 그림판 + 폰으로 들어와 같은 그림을 함께 그린다.

## 구성

```
 [폰] cam.html                       [PC] canvas.html (플레이어마다 1개)
  MediaPipe Hand Landmarker (JS)       그림판 + 내 폰용 QR
        │ wss /ws/cam/{sid}/{pid}          ▲ wss /ws/canvas/{sid}/{pid}
        ▼                                  │  (방의 모든 플레이어 제스처 수신)
 ┌──────────────── edge (Nginx, HTTPS) ────────────────┐
 │  /            → web/ 정적 파일                       │
 │  /api, /ws    → c1_gateway:8000                     │
 └─────────────────────────────────────────────────────┘
        │
   c1_gateway (FastAPI)  ── ws /ws/{sid}-{pid} ──▶  c2_gesture (FastAPI)
   방·플레이어·QR 발급,  ◀──── GestureEvent ─────  제스처 판정 + 스무딩
   방 전체로 브로드캐스트                            (폰 연결마다 엔진 1개)
```

| 폴더 | 역할 |
|---|---|
| `c1_gateway/` | 방 생성, 플레이어 입장(색 배정 + 폰용 QR 발급), cam → c2 → 방의 모든 canvas 중계, 펜/지우기/스냅샷 브로드캐스트 |
| `c2_gesture/` | 21개 랜드마크 → 제스처 판정, One Euro Filter 로 좌표 스무딩, 디바운스 |
| `web/` | `cam.html`(폰, MediaPipe JS), `canvas.html`(PC, 그림판) |
| `edge/` | Nginx. TLS 종료 (폰은 HTTPS 에서만 카메라 허용), 정적 파일 + 프록시 |
| `common/schema.py` | 메시지 규격 (pydantic). 두 서비스 이미지에 함께 복사됨 |
| `scripts/make-cert.sh` | 자체 서명 인증서 생성 (LAN IP 를 SAN 에 포함) |

랜드마크 추출은 폰(브라우저)에서, 판정은 서버(c2)에서 한다. 영상이 아닌
좌표(21×3 floats)만 전송하므로 트래픽이 작다.

## 흐름

1. 방장: `https://<주소>/` 접속 → `canvas.html` 이 방 생성(`POST /api/session`), 주소가 `canvas.html?room=<sid>` 로 바뀜
2. 친구: 방장이 "친구 초대"로 복사한 `canvas.html?room=<sid>` 접속
3. 각 그림판이 플레이어로 입장(`POST /api/session/{sid}/players`) → 플레이어 ID·색 배정, 자기 폰용 QR 표시.
   플레이어 ID 는 sessionStorage 에 저장해 새로고침해도 같은 자리(색·폰 연결) 유지 (`PLAYER_IDLE_SEC` 동안)
4. 각자 폰으로 자기 QR 스캔 → `cam.html?s=<sid>&p=<pid>&c=<색>` → 카메라 + Hand Landmarker 실행
5. 폰이 프레임마다 `LandmarkFrame` 전송 (최대 30fps, 손이 사라지면 빈 배열 1회)
6. gateway 가 c2 로 전달 → c2 가 `GestureEvent` 반환 → gateway 가 `player` 를 붙여 **방의 모든 그림판**으로 전달
7. 입장/퇴장/폰 연결이 바뀌면 모든 그림판에 `PlayersEvent` 전송 (내 폰이 연결되면 QR 숨김)

### 함께 그리기 동기화

- 그림 자체는 서버에 저장하지 않는다. 모든 그림판이 같은 이벤트를 같은 순서로 받아 **각자 똑같이 그린다**
  (좌표가 0~1 정규화라 화면 크기가 달라도 됨)
- 그림판은 플레이어별 펜 상태(색, 굵기, 그리던 선, 지우개 연결점)를 따로 관리
- 툴바 색/굵기/전체 지우기는 그림판 → gateway → 방 전체(보낸 쪽 포함)로 돌아온 뒤 적용해 순서를 맞춤.
  제스처 색상 창(swipe)과 🤘 전체 지우기는 GestureEvent 를 받은 각 그림판이 직접 반영
- 👍 저장은 제스처를 한 사람의 PC 에서만 다운로드
- 늦게 들어온 그림판: gateway 가 기존 그림판 하나에 `snapshot_request` → 기존 그림판이 현재 그림 PNG + 펜 상태를
  `snapshot` 으로 보냄 → 새 그림판은 입장 후 그려진 선 **아래에** 깔아 합침
- 손 골격·커서는 플레이어 색으로 커서 레이어에만 그림 (PNG 저장에 안 들어감). 폰 화면의 랜드마크 점도 같은 색

## 메시지 규격 (`common/schema.py`)

```jsonc
// cam → c2
{"type":"landmarks","ts":1234.5,"mirrored":true,"handedness":"Right",
 "landmarks":[[x,y,z], ... 21개]}          // 손 없음 = []
// c2 → gateway → 방의 모든 canvas (player 는 gateway 가 채움)
{"type":"gesture","gesture":"draw","x":0.62,"y":0.37,"ts":1234.5,"landmarks":[[x,y],...],"player":"aB3x_9"}
// gateway → 방의 모든 canvas
{"type":"players","players":[{"id":"aB3x_9","color":"#e5484d","cam":true,"canvas":true}, ...]}
// canvas → gateway → 방의 모든 canvas (player 는 gateway 가 보낸 쪽으로 덮어씀)
{"type":"pen","color_idx":3,"width":8,"player":"aB3x_9"}
{"type":"clear","player":"aB3x_9"}
// gateway → 기존 canvas / 기존 canvas → gateway → 새 canvas
{"type":"snapshot_request","to":"<새 pid>"}
{"type":"snapshot","to":"<새 pid>","image":"data:image/png;base64,...","pens":{"<pid>":{"color_idx":0,"width":6}}}
```

WebSocket 종료 코드: `4404` 없는 방/플레이어, `4409` 같은 플레이어의 새 연결로 교체됨.
REST: `409` 방이 가득 참 (최대 6명 = 플레이어 색 개수).

## 제스처

| 손 모양 | 제스처 | 동작 |
|---|---|---|
| ☝ 검지만 | `draw` | 검지 끝(8)으로 선 그리기 |
| ✌ 검지+중지 | `hover` | 커서만 이동 |
| 🤏 엄지+검지 끝 맞대기 | `erase` | 두 손가락이 맞닿은 점 위치 지우개 |
| 🖐 그 외 (손바닥 등 애매한 모양) | `hover` | 커서만 이동 |
| ✊ 주먹 | `idle` | 대기 |
| 👍 엄지 척 | `save` | 그림판을 PNG 로 자동 저장 |
| 🤘 검지+새끼 | `clear` | 약 1초 유지하면 전체 지우기 |
| ✌ 검지·중지 붙이고 흔들기 | `palette` | 색상 창 열기, 붙인 채 좌우로 움직여 색 선택, 손가락을 떼면 확정 |
| 손 없음 | `none` | 선 끊기 |

- 손가락 펴짐: `|tip−wrist| > |pip−wrist| × EXTEND_RATIO` (손 회전에 강함)
- 디바운스: 같은 제스처가 `STABLE_FRAMES` 연속일 때만 전환 (`save`/`clear` 는 오작동 방지로 `SAVE_FRAMES`/`CLEAR_FRAMES`).
  전환 중에는 `GestureEvent.hold`(0~1)로 진행률을 보내 그림판이 커서 둘레에 원호로 표시
- 선 끊김 방지: `draw` 중에는 검지를 PIP 대신 뿌리(5) 기준으로 판정해 굽어도 유지하고(`DRAW_INDEX_RATIO`) 다른 손가락 기준을 높이며(`DRAW_OTHER_RATIO`),
  `draw` 에서 벗어나려면 `DRAW_RELEASE_FRAMES` 연속 필요. 손을 놓쳤다가 `LOST_GRACE_MS` 안에 다시 찾으면 이전 제스처 유지
- 핀치: 다른 손가락보다 먼저 판정. `|엄지끝(4)−검지끝(8)| < PINCH_RATIO × 손크기(손목−9)` 이고,
  주먹과 구분하려고 검지 끝이 검지 뿌리(5)보다 손목에서 멀어야 함. 지우는 중에는 `PINCH_RELEASE_RATIO` 로 느슨하게(깜빡임 방지)
- 엄지 척: 네 손가락 접힘 + 엄지 끝(4)이 다른 모든 점보다 `THUMB_MARGIN`×손크기 이상 높고 위를 향함
- 저장: 그림판이 `save` 로 바뀌는 순간 1회 다운로드 (`airsketch-날짜-시각.png`, 흰 배경)
- 전체 지우기: 그림판이 `clear` 로 바뀌는 순간 1회 실행. `save`/`clear` 는 손을 잠깐 놓쳤다 찾아도 재실행되지 않도록 2초 쿨다운
- 색상 창 열기: `hover` 이면서 검지·중지 끝 거리 < 두 MCP(5·9) 거리 × `TOGETHER_RATIO` 일 때, 검지 끝 x(스무딩 전)가
  `SHAKE_AMP` 이상씩 `SHAKE_WINDOW_MS` 안에 `SHAKE_TURNS` 번 방향을 바꾸면 `palette`. 손가락을 붙인 동안 유지되고, 흔드는 중엔 `hold` 로 진행률 전송
- 색상 스와이프: `palette` 동안 c2 가 검지 끝 x(스무딩 후)를 기준점과 비교해 `SWIPE_DIST` 이상 움직이면
  `GestureEvent.swipe`(±1)를 보내고 기준점을 옮김. 그림판은 한 칸씩 색을 넘기고(양 끝은 순환) `palette` 가 끝나면 창을 닫음
- 좌표: 전면 카메라 좌우 반전 → 가장자리 여백(좌우 `ACTIVE_MARGIN`, 위 `ACTIVE_TOP`, 아래 `ACTIVE_BOTTOM`) 잘라 0~1 로 확장 → One Euro Filter
  - 아래 여백을 크게 두는 이유: 검지 끝은 손 맨 위라, 카메라 맨 아래까지 내리면 손바닥이 화면 밖으로 나가 인식이 끊김

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

### 빌드 없이 최신 main 실행

main 에 합쳐질 때마다 CD 가 이미지를 GHCR 에 올리므로, 코드를 고치지 않고 실행만 할 때는 빌드가 필요 없다.

```bash
docker compose pull                # ghcr.io/won-y/gesture-remote-drawing/* 최신 이미지 받기
docker compose up -d
```

특정 커밋 버전은 `IMAGE_TAG=<커밋 SHA> docker compose up -d`.

## CI/CD (`.github/workflows/ci-cd.yml`)

| 단계 | 언제 | 내용 |
|---|---|---|
| 코드 검사 + 단위 테스트 | PR, main 푸시 | `ruff check .` → `pytest` (`tests/test_gesture.py`: 합성 랜드마크로 제스처 판정 검증) |
| 통합 테스트 | 위 통과 후 | 인증서 생성 → `docker compose up --build --wait` → `scripts/smoke_test.py` (세션 → 그림판·폰 WS → 랜드마크 → 그림판에 `draw` 도착 확인) |
| 이미지 배포 | main 푸시 + 테스트 통과 | `edge`·`c1_gateway`·`c2_gesture` 이미지를 `ghcr.io/won-y/gesture-remote-drawing/<서비스>:latest`, `:<커밋 SHA>` 로 푸시 |

로컬에서 같은 검사 돌리기:

```bash
pip install -r requirements-dev.txt   # Python 3.12 권장
ruff check . && pytest
# 서비스를 띄운 상태에서
python scripts/smoke_test.py https://localhost
```

제스처 판정(`c2_gesture/gesture.py`)을 바꾸면 `tests/test_gesture.py` 에 해당 손 모양 테스트를 추가한다.
