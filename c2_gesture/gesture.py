"""랜드마크 → 제스처 판정 + 좌표 스무딩(One Euro Filter).

MediaPipe Hand Landmarker 인덱스:
  0 손목 / 5·9·13·17 각 손가락 MCP / 6·10·14·18 PIP / 8·12·16·20 끝(TIP)
"""
import math
import os

from common.schema import Gesture, GestureEvent, LandmarkFrame

WRIST = 0
INDEX_TIP, MIDDLE_MCP = 8, 9
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]  # (tip, pip): 검지, 중지, 약지, 새끼

EXTEND_RATIO = float(os.getenv("EXTEND_RATIO", "1.1"))   # tip-손목 거리 / pip-손목 거리
STABLE_FRAMES = int(os.getenv("STABLE_FRAMES", "3"))     # 제스처 전환에 필요한 연속 프레임
ACTIVE_MARGIN = float(os.getenv("ACTIVE_MARGIN", "0.1"))  # 카메라 가장자리 여백(0~0.5)
MIN_CUTOFF = float(os.getenv("FILTER_MIN_CUTOFF", "1.5"))
BETA = float(os.getenv("FILTER_BETA", "5.0"))


class OneEuroFilter:
    """https://gery.casiez.net/1euro/ — 느릴 땐 떨림 제거, 빠를 땐 지연 최소화."""

    def __init__(self, min_cutoff: float = MIN_CUTOFF, beta: float = BETA, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.x_prev: float | None = None
        self.dx_prev = 0.0
        self.t_prev = 0.0

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self.x_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = max(t - self.t_prev, 1e-6)
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(dt, self.d_cutoff)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        a = self._alpha(dt, self.min_cutoff + self.beta * abs(dx_hat))
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def finger_states(lm) -> list[bool]:
    """[검지, 중지, 약지, 새끼] 펴짐 여부. 손 방향과 무관하도록 손목 거리로 비교."""
    w = lm[WRIST]
    return [_dist(lm[tip], w) > _dist(lm[pip], w) * EXTEND_RATIO for tip, pip in FINGERS]


def classify(lm) -> Gesture:
    i, m, r, p = finger_states(lm)
    if i and not (m or r or p):
        return Gesture.DRAW
    if i and m and not (r or p):
        return Gesture.HOVER
    if i and m and r and p:
        return Gesture.ERASE
    if not (i or m or r or p):
        return Gesture.IDLE
    return Gesture.HOVER  # 애매한 손 모양은 그리지 않고 커서만 이동


def _to_canvas(v: float) -> float:
    """카메라 가장자리 여백을 잘라내 그림판 끝까지 손이 닿도록 확장."""
    span = 1 - 2 * ACTIVE_MARGIN
    return min(1.0, max(0.0, (v - ACTIVE_MARGIN) / span))


class GestureEngine:
    """연결(세션)마다 하나씩 생성. 상태: 현재 제스처, 후보 제스처, 필터."""

    def __init__(self):
        self.gesture = Gesture.NONE
        self.candidate = Gesture.NONE
        self.count = 0
        self.fx, self.fy = OneEuroFilter(), OneEuroFilter()

    def _debounce(self, g: Gesture) -> Gesture:
        if g == self.gesture:
            self.candidate, self.count = g, 0
        elif g == self.candidate:
            self.count += 1
            if self.count >= STABLE_FRAMES:
                self.gesture, self.count = g, 0
        else:
            self.candidate, self.count = g, 1
        return self.gesture

    def update(self, frame: LandmarkFrame) -> GestureEvent | None:
        if not frame.landmarks:
            if self.gesture == Gesture.NONE:
                return None
            self.gesture = self.candidate = Gesture.NONE
            self.count = 0
            self.fx.reset()
            self.fy.reset()
            return GestureEvent(gesture=Gesture.NONE, ts=frame.ts)

        lm = frame.landmarks
        g = self._debounce(classify(lm))
        pt = lm[MIDDLE_MCP] if g == Gesture.ERASE else lm[INDEX_TIP]
        x = 1 - pt[0] if frame.mirrored else pt[0]
        t = frame.ts / 1000.0
        return GestureEvent(
            gesture=g,
            x=round(self.fx(_to_canvas(x), t), 4),
            y=round(self.fy(_to_canvas(pt[1]), t), 4),
            ts=frame.ts,
        )
