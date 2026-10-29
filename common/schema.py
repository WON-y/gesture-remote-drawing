"""폰(cam) ↔ c1_gateway ↔ c2_gesture ↔ 그림판(canvas) 사이의 메시지 규격.

모든 메시지는 JSON 텍스트 프레임이며 `type` 필드로 구분한다.
JS 쪽(web/*.html)도 이 규격을 그대로 따른다.
"""
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

NUM_LANDMARKS = 21  # MediaPipe Hand Landmarker 랜드마크 개수


class Gesture(str, Enum):
    NONE = "none"    # 손이 화면에 없음
    IDLE = "idle"    # 주먹: 아무 동작 안 함
    HOVER = "hover"  # 검지+중지: 커서만 이동
    DRAW = "draw"    # 검지만: 그리기
    ERASE = "erase"  # 핀치(엄지+검지 끝 맞대기): 지우개
    SAVE = "save"    # 엄지 척: 그림 저장
    CLEAR = "clear"  # 검지+새끼(🤘) 길게 유지: 전체 지우기
    PALETTE = "palette"  # 검지·중지 붙이고 흔들기: 색상 창 열기, 붙인 채 좌우 스와이프로 색 선택


# ---- cam → gateway → c2 ----
class LandmarkFrame(BaseModel):
    type: Literal["landmarks"] = "landmarks"
    ts: float  # 폰의 performance.now() (ms)
    mirrored: bool = True  # 전면 카메라면 True → x 좌우 반전
    handedness: str | None = None
    # [[x, y, z], ...] 정규화 좌표(0~1). 빈 배열 = 손 없음
    landmarks: list[tuple[float, float, float]] = Field(default_factory=list)

    @field_validator("landmarks")
    @classmethod
    def _check_len(cls, v):
        if v and len(v) != NUM_LANDMARKS:
            raise ValueError(f"landmarks must have 0 or {NUM_LANDMARKS} points")
        return v


# ---- c2 → gateway → canvas ----
class GestureEvent(BaseModel):
    type: Literal["gesture"] = "gesture"
    gesture: Gesture
    x: float | None = None  # 그림판 기준 정규화 좌표(0~1), 스무딩 적용됨
    y: float | None = None
    ts: float
    # 그림판 좌표(0~1)로 변환한 21개 점 [[x, y], ...]. 표시용이라 스무딩은 적용하지 않음
    landmarks: list[tuple[float, float]] | None = None
    # 길게 유지해야 하는 제스처(save/clear)로 바뀌는 중이거나 palette 를 열려고 흔드는 중일 때 진행률 0~1
    hold: float | None = None
    # palette 중 좌우 스와이프 한 칸: -1 왼쪽, 1 오른쪽
    swipe: int | None = None
    # 이 제스처를 만든 플레이어. c2 는 비워 두고 gateway 가 채워서 방의 모든 그림판에 보냄
    player: str | None = None


# ---- gateway → 방의 모든 canvas: 참여자 목록 (입장/퇴장/폰 연결이 바뀔 때마다) ----
class PlayerState(BaseModel):
    id: str
    color: str  # 손·커서 표시 색 (플레이어마다 다름)
    cam: bool  # 폰 연결 여부
    canvas: bool  # 그림판 연결 여부


class PlayersEvent(BaseModel):
    type: Literal["players"] = "players"
    players: list[PlayerState]


# ---- canvas → gateway → 방의 모든 canvas ----
# player 는 gateway 가 보낸 쪽 기준으로 덮어씀 (클라이언트 값은 무시)
class PenEvent(BaseModel):
    """툴바에서 펜 색/굵기/브러시를 바꿈. 제스처 색상 창(swipe)은 GestureEvent 로 각 그림판이 직접 반영."""
    type: Literal["pen"] = "pen"
    player: str | None = None
    color_idx: int | None = Field(None, ge=0, le=63)
    width: int | None = Field(None, ge=2, le=30)
    brush_idx: int | None = Field(None, ge=0, le=63)


class ClearEvent(BaseModel):
    """툴바의 전체 지우기 버튼. 🤘 제스처는 GestureEvent 로 각 그림판이 직접 처리."""
    type: Literal["clear"] = "clear"
    player: str | None = None


class PenState(BaseModel):
    color_idx: int = Field(ge=0, le=63)
    width: int = Field(ge=2, le=30)
    brush_idx: int = Field(0, ge=0, le=63)


# ---- 늦게 들어온 그림판 동기화 ----
# gateway → 기존 canvas: SnapshotRequest(to=새 플레이어)
# 기존 canvas → gateway → 새 canvas: Snapshot(현재 그림 PNG + 펜 상태)
class SnapshotRequest(BaseModel):
    type: Literal["snapshot_request"] = "snapshot_request"
    to: str


class Snapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    to: str
    image: str = Field(pattern=r"^data:image/png;base64,", max_length=8_000_000)
    pens: dict[str, PenState] = Field(default_factory=dict)


CanvasMessage = Annotated[PenEvent | ClearEvent | Snapshot, Field(discriminator="type")]


# ---- REST ----
class SessionInfo(BaseModel):
    """POST /api/session: 방 생성"""
    session_id: str
    invite_url: str  # 친구에게 보낼 그림판 링크


class PlayerInfo(BaseModel):
    """POST /api/session/{sid}/players: 방에 플레이어로 입장 (자기 폰 연결용 QR 발급)"""
    session_id: str
    player_id: str
    color: str
    cam_url: str
    qr_svg: str
    invite_url: str


# ---- REST: /api/gallery ----
class GalleryItem(BaseModel):
    id: str
    title: str
    author: str
    created: float  # unix time (s)
    width: int
    height: int
    url: str        # 원본 PNG
    thumb_url: str  # 목록용 썸네일
    mine: bool = False  # 요청한 X-Owner-Id 가 올린 그림인지


class GalleryPage(BaseModel):
    items: list[GalleryItem]
    total: int
