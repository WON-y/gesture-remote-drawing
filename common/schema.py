"""폰(cam) ↔ c1_gateway ↔ c2_gesture ↔ 그림판(canvas) 사이의 메시지 규격.

모든 메시지는 JSON 텍스트 프레임이며 `type` 필드로 구분한다.
JS 쪽(web/*.html)도 이 규격을 그대로 따른다.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

NUM_LANDMARKS = 21  # MediaPipe Hand Landmarker 랜드마크 개수


class Gesture(str, Enum):
    NONE = "none"    # 손이 화면에 없음
    IDLE = "idle"    # 주먹: 아무 동작 안 함
    HOVER = "hover"  # 검지+중지: 커서만 이동
    DRAW = "draw"    # 검지만: 그리기
    ERASE = "erase"  # 핀치(엄지+검지 끝 맞대기): 지우개
    SAVE = "save"    # 엄지 척: 그림 저장


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


# ---- gateway → canvas ----
class PeerEvent(BaseModel):
    type: Literal["peer"] = "peer"
    role: Literal["cam", "canvas"]
    state: Literal["joined", "left"]


# ---- REST: POST /api/session ----
class SessionInfo(BaseModel):
    session_id: str
    cam_url: str
    qr_svg: str
