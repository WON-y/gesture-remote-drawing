# AirSketch — 제스처 원격 드로잉

폰 카메라로 손을 인식해서 PC 브라우저 그림판에 **허공 제스처로 그림을 그리는** 웹 앱입니다.
한 방에 최대 6명이 각자 PC 그림판과 폰을 연결해 같은 그림을 함께 그릴 수 있고, 완성한 그림은 갤러리에 공유할 수 있습니다.

- 폰 브라우저에서 MediaPipe Hand Landmarker로 손 랜드마크(21개 점)를 뽑고, 영상이 아니라 **좌표만** 서버로 보냅니다
- 서버(c2)가 손 모양을 제스처로 판정하고 One Euro Filter로 좌표 떨림을 줄입니다
- 방에 있는 모든 그림판이 같은 이벤트를 받아 각자 똑같이 그립니다. 늦게 들어온 사람은 기존 그림을 스냅샷으로 받습니다
- 앱 설치 없이 PC와 폰 모두 브라우저만 있으면 됩니다

## 구성

```
 [폰] cam.html                       [PC] canvas.html (플레이어마다 1개)
  MediaPipe Hand Landmarker (JS)       그림판 + 내 폰용 QR
        │ wss /ws/cam/{sid}/{pid}          ▲ wss /ws/canvas/{sid}/{pid}
        ▼                                  │
 ┌──────────────── edge (Nginx, HTTPS) ────────────────┐
 │  /            → web/ 정적 파일                       │
 │  /api, /ws    → c1_gateway:8000                     │
 └─────────────────────────────────────────────────────┘
        │
   c1_gateway (FastAPI)  ── ws ──▶  c2_gesture (FastAPI)
   방·플레이어·QR 발급,  ◀─ 제스처 ─  제스처 판정 + 스무딩
   방 전체 브로드캐스트, 갤러리
```

| 폴더 | 역할 |
|---|---|
| [c1_gateway/](c1_gateway/) | 방 생성, 플레이어 입장(색 배정, 폰용 QR 발급), 폰 → c2 → 그림판 중계, 갤러리 API |
| [c2_gesture/](c2_gesture/) | 랜드마크 → 제스처 판정, 좌표 스무딩, 디바운스 |
| [web/](web/) | `canvas.html`(PC 그림판), `cam.html`(폰 카메라), `gallery.html`(갤러리) |
| [edge/](edge/) | Nginx. TLS 종료(폰 브라우저는 HTTPS에서만 카메라를 허용), 정적 파일 서빙, 프록시 |
| [common/schema.py](common/schema.py) | 서비스 간 메시지 규격 (pydantic) |
| [scripts/](scripts/) | 자체 서명 인증서 생성, 통합 테스트 |

**기술 스택:** Python 3.12, FastAPI, WebSocket, MediaPipe (JS), Nginx, SQLite, Docker Compose, GitHub Actions

## 제스처

| 손 모양 | 동작 |
|---|---|
| ☝ 검지만 펴기 | 그리기 |
| ✌ 검지 + 중지 | 커서만 이동 |
| 🤏 엄지와 검지 끝 맞대기 | 지우개 |
| ✊ 주먹 | 대기 |
| 👍 엄지 척 (잠시 유지) | 그림을 PNG로 저장 |
| 🤘 검지 + 새끼 (약 1초 유지) | 전체 지우기 |
| ✌ 검지·중지 붙이고 흔들기 | 색상 창 열기 → 붙인 채 좌우로 움직여 색 선택 → 손가락을 떼면 확정 |

툴바에서도 색, 굵기, 브러시(기본 / 붓 / 수채), 전체 지우기를 바꿀 수 있습니다.

## 실행 방법

### 준비물

- Docker, Docker Compose
- `openssl` (인증서 생성용. macOS/Linux는 기본 설치, Windows는 Git Bash 사용)
- PC와 폰이 **같은 Wi-Fi**에 연결되어 있어야 합니다 (외부에서 접속하려면 아래 [Cloudflare Tunnel](#외부에서-접속하기-cloudflare-tunnel) 참고)
- 폰은 MediaPipe 모델을 CDN에서 받으므로 인터넷 연결이 필요합니다

### 1. 환경 변수 설정

```bash
cp .env.example .env
```

`.env`의 `PUBLIC_HOST`에 PC의 LAN IP를 입력합니다. 폰용 QR 코드에 이 주소가 들어갑니다.

```bash
# PC의 LAN IP 확인
ipconfig getifaddr en0          # macOS
hostname -I                     # Linux
ipconfig                        # Windows (IPv4 주소)
```

443/80 포트를 이미 쓰고 있다면 `HTTPS_PORT`, `HTTP_PORT`를 바꿉니다 (예: 8443 / 8080).

### 2. 인증서 생성

폰 브라우저는 HTTPS에서만 카메라를 열 수 있어서 자체 서명 인증서가 필요합니다.

```bash
bash scripts/make-cert.sh       # .env 의 PUBLIC_HOST 로 edge/certs/ 에 생성
```

### 3. 서비스 실행

```bash
docker compose up -d --build
```

코드를 고치지 않고 실행만 할 때는 빌드 없이 GHCR에 올라간 최신 이미지를 받아도 됩니다.

```bash
docker compose pull
docker compose up -d
```

### 4. 접속해서 그리기

1. **PC**: `https://<PUBLIC_HOST>:<HTTPS_PORT>/` 접속 → 인증서 경고가 나오면 "계속 진행"
2. 그림판이 열리면서 방이 만들어지고, 화면에 내 폰용 QR 코드가 표시됩니다
3. **폰**: QR 코드 스캔 → 인증서 경고에서 "계속 진행" → **카메라 시작**
4. 폰을 세워 두고 카메라 앞에서 손을 움직이면 PC 그림판에 그려집니다
5. 친구와 함께 그리려면 **친구 초대** 버튼으로 링크를 복사해 보내세요. 친구도 자기 PC에서 링크를 열고 자기 폰으로 QR을 스캔하면 됩니다
6. 다 그렸으면 **공유하기**로 갤러리(`/gallery.html`)에 올릴 수 있습니다. 공유한 그림은 `./data/gallery/`에 저장되어 컨테이너를 다시 빌드해도 남습니다

종료:

```bash
docker compose down
```

### 외부에서 접속하기 (Cloudflare Tunnel)

같은 Wi-Fi가 아니어도 인터넷으로 접속할 수 있습니다. 이때도 2단계 인증서는 만들어 두어야 edge가 시작됩니다.

```bash
# 임시 주소 (계정·도메인 필요 없음, 실행할 때마다 주소가 바뀜)
docker compose --profile quick-tunnel up -d
docker compose logs tunnel-quick | grep trycloudflare.com   # 발급된 주소 확인

# 고정 주소 (Cloudflare 도메인 + Zero Trust 터널 토큰)
# .env 에 TUNNEL_TOKEN 입력, Public Hostname 은 http://edge:8081 로 지정
docker compose --profile tunnel up -d
```

터널 주소는 정식 인증서를 쓰므로 인증서 경고가 나오지 않습니다.

### 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 폰에서 QR 주소로 접속이 안 됨 | PC와 폰이 같은 Wi-Fi인지, `.env`의 `PUBLIC_HOST`가 PC의 현재 IP인지, 방화벽이 해당 포트 인바운드를 막고 있지 않은지 |
| `PUBLIC_HOST`를 바꿨는데 인증서 오류 | `bash scripts/make-cert.sh`를 다시 실행하고 `docker compose restart edge` |
| 그림판 아래쪽까지 손이 안 닿음 | `.env`의 `ACTIVE_BOTTOM`을 키움 |
| 지우개(핀치)가 잘 안 잡힘 / 그리다가 지워짐 | `PINCH_RATIO`를 키움 / 줄임 |
| 색상 창이 잘 안 열림 / ✌에서도 열림 | `TOGETHER_RATIO`를 키움 / 줄임 |

`.env`를 고친 뒤에는 `docker compose up -d`로 다시 적용합니다. 제스처 튜닝 값은 [.env.example](.env.example)에 설명이 있습니다.

## 개발

```bash
pip install -r requirements-dev.txt   # Python 3.12 권장
ruff check .                          # 코드 검사
pytest                                # 제스처 판정 단위 테스트
python scripts/smoke_test.py https://localhost   # 서비스를 띄운 상태에서 통합 테스트
```

`web/` 폴더는 edge 컨테이너에 마운트되어 있어서 HTML을 고치면 새로고침만으로 반영됩니다.
제스처 판정([c2_gesture/gesture.py](c2_gesture/gesture.py))을 바꾸면 [tests/test_gesture.py](tests/test_gesture.py)에 해당 손 모양 테스트를 추가해 주세요.

### CI/CD

[.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml)

1. PR과 main 푸시마다 `ruff` → `pytest` 실행
2. 통과하면 `docker compose`로 전체 서비스를 띄우고 통합 테스트 실행
3. main에 합쳐지면 `edge`, `c1_gateway`, `c2_gesture` 이미지를 `ghcr.io/won-y/gesture-remote-drawing/<서비스>`에 `latest`, 커밋 SHA 태그로 배포

특정 커밋 버전을 실행하려면 `IMAGE_TAG=<커밋 SHA> docker compose up -d`를 씁니다.

## 더 보기

- 메시지 규격, 동기화 방식, 제스처 판정 세부 조건, 갤러리 API: [docs/design.md](docs/design.md)
- 브러시 텍스처 라이선스: [web/brushes/](web/brushes/)
