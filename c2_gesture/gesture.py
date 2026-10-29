"""랜드마크 → 제스처 판정 + 좌표 스무딩(One Euro Filter).

MediaPipe Hand Landmarker 인덱스:
  0 손목 / 2 엄지 MCP, 4 엄지 끝 / 5·9·13·17 각 손가락 MCP / 6·10·14·18 PIP / 8·12·16·20 끝(TIP)
"""
import math
import os

from common.schema import Gesture, GestureEvent, LandmarkFrame

WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_TIP, MIDDLE_MCP = 8, 9
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]  # (tip, pip): 검지, 중지, 약지, 새끼

EXTEND_RATIO = float(os.getenv("EXTEND_RATIO", "1.1"))   # tip-손목 거리 / pip-손목 거리
# 그리는 중(draw)에는 판정을 느슨하게: 검지는 덜 펴져도, 다른 손가락은 더 확실히 펴져야 인정
DRAW_INDEX_RATIO = float(os.getenv("DRAW_INDEX_RATIO", "1.0"))
DRAW_OTHER_RATIO = float(os.getenv("DRAW_OTHER_RATIO", "1.25"))
STABLE_FRAMES = int(os.getenv("STABLE_FRAMES", "3"))     # 제스처 전환에 필요한 연속 프레임
DRAW_RELEASE_FRAMES = int(os.getenv("DRAW_RELEASE_FRAMES", "6"))  # draw 에서 빠져나갈 때 필요한 연속 프레임
LOST_GRACE_MS = float(os.getenv("LOST_GRACE_MS", "300"))  # 손을 잠깐 놓쳤다 찾으면 이전 제스처 이어가기
SAVE_FRAMES = int(os.getenv("SAVE_FRAMES", "15"))       # 엄지 척 유지 프레임(오작동 방지로 더 길게)
CLEAR_FRAMES = int(os.getenv("CLEAR_FRAMES", "30"))     # 🤘 유지 프레임(약 1초, 전체 지우기라 가장 길게)
PALETTE_FRAMES = int(os.getenv("PALETTE_FRAMES", "8"))  # 3손가락 유지 프레임(색상 창 열기)
SWIPE_DIST = float(os.getenv("SWIPE_DIST", "0.12"))     # 색상 한 칸 넘기는 데 필요한 좌우 이동(그림판 폭 비율)
THUMB_MARGIN = float(os.getenv("THUMB_MARGIN", "0.2"))  # 엄지 끝이 나머지보다 높아야 하는 정도(손 크기 비율)
ACTIVE_MARGIN = float(os.getenv("ACTIVE_MARGIN", "0.1"))  # 카메라 좌우 여백(0~0.5)
ACTIVE_TOP = float(os.getenv("ACTIVE_TOP", "0.05"))       # 카메라 위 여백
# 카메라 아래 여백. 검지 끝은 손 맨 위라 끝까지 내리면 손바닥이 화면 밖으로 나가 인식이 끊기므로 크게 둠
ACTIVE_BOTTOM = float(os.getenv("ACTIVE_BOTTOM", "0.4"))
MIN_CUTOFF = float(os.getenv("FILTER_MIN_CUTOFF", "1.5"))
BETA = float(os.getenv("FILTER_BETA", "5.0"))

# 오작동 방지로 STABLE_FRAMES 보다 오래 유지해야 전환되는 제스처
HOLD_FRAMES = {Gesture.SAVE: SAVE_FRAMES, Gesture.CLEAR: CLEAR_FRAMES, Gesture.PALETTE: PALETTE_FRAMES}


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


def finger_states(lm, ratios=(EXTEND_RATIO,) * 4) -> list[bool]:
    """[검지, 중지, 약지, 새끼] 펴짐 여부. 손 방향과 무관하도록 손목 거리로 비교."""
    w = lm[WRIST]
    return [_dist(lm[tip], w) > _dist(lm[pip], w) * k for (tip, pip), k in zip(FINGERS, ratios)]


def thumb_up(lm) -> bool:
    """엄지 끝이 손에서 가장 높은 점이고(이미지 y 최소), 엄지가 위쪽을 향함."""
    tip, mcp = lm[THUMB_TIP], lm[THUMB_MCP]
    size = _dist(lm[WRIST], lm[MIDDLE_MCP])
    top = min(pt[1] for k, pt in enumerate(lm) if k > THUMB_TIP)  # 엄지 외 손가락 중 가장 높은 점
    dx, dy = tip[0] - mcp[0], tip[1] - mcp[1]
    return tip[1] < top - THUMB_MARGIN * size and -dy > abs(dx)


def classify(lm, drawing: bool = False) -> Gesture:
    ratios = (DRAW_INDEX_RATIO,) + (DRAW_OTHER_RATIO,) * 3 if drawing else (EXTEND_RATIO,) * 4
    i, m, r, p = finger_states(lm, ratios)
    if i and not (m or r or p):
        return Gesture.DRAW
    if i and m and not (r or p):
        return Gesture.HOVER
    if i and m and r and p:
        return Gesture.ERASE
    if i and p and not (m or r):
        return Gesture.CLEAR
    if i and m and r and not p:
        return Gesture.PALETTE
    if not (i or m or r or p):
        return Gesture.SAVE if thumb_up(lm) else Gesture.IDLE
    return Gesture.HOVER  # 애매한 손 모양은 그리지 않고 커서만 이동


def _scale(v: float, lo: float, hi: float) -> float:
    """카메라 좌표 [lo, hi] 구간을 그림판 0~1로 확장 (가장자리 여백을 잘라 그림판 끝까지 손이 닿도록)."""
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))


def _to_canvas_x(v: float) -> float:
    return _scale(v, ACTIVE_MARGIN, 1 - ACTIVE_MARGIN)


def _to_canvas_y(v: float) -> float:
    return _scale(v, ACTIVE_TOP, 1 - ACTIVE_BOTTOM)


class GestureEngine:
    """연결(세션)마다 하나씩 생성. 상태: 현재 제스처, 후보 제스처, 필터."""

    def __init__(self):
        self.gesture = Gesture.NONE
        self.candidate = Gesture.NONE
        self.count = 0
        self.lost: tuple[Gesture, float] | None = None  # (놓치기 직전 제스처, 놓친 시각 ms)
        self.swipe_x: float | None = None  # palette 중 스와이프 기준 x (한 칸 넘길 때마다 갱신)
        self.fx, self.fy = OneEuroFilter(), OneEuroFilter()

    def _need(self, g: Gesture) -> int:
        if g in HOLD_FRAMES:
            return HOLD_FRAMES[g]
        if self.gesture == Gesture.DRAW:
            return DRAW_RELEASE_FRAMES
        return STABLE_FRAMES

    def _debounce(self, g: Gesture) -> Gesture:
        if g == self.gesture:
            self.candidate, self.count = g, 0
        elif g == self.candidate:
            self.count += 1
            if self.count >= self._need(g):
                self.gesture, self.count = g, 0
        else:
            self.candidate, self.count = g, 1
        return self.gesture

    def _hold(self) -> float | None:
        """길게 유지해야 하는 제스처로 바뀌는 중이면 진행률(0~1)."""
        c = self.candidate
        if c in HOLD_FRAMES and c != self.gesture and self.count > 0:
            return round(self.count / HOLD_FRAMES[c], 2)
        return None

    def _swipe(self, g: Gesture, x: float) -> int | None:
        """palette 중 기준점에서 SWIPE_DIST 이상 움직이면 한 칸(±1)."""
        if g != Gesture.PALETTE:
            self.swipe_x = None
            return None
        if self.swipe_x is None:
            self.swipe_x = x
            return None
        dx = x - self.swipe_x
        if abs(dx) < SWIPE_DIST:
            return None
        self.swipe_x = x
        return 1 if dx > 0 else -1

    def update(self, frame: LandmarkFrame) -> GestureEvent | None:
        if not frame.landmarks:
            # 디바운스 중(candidate 있음)에 손이 사라져도 그림판의 골격을 지우도록 알림
            if self.gesture == Gesture.NONE and self.candidate == Gesture.NONE:
                return None
            self.lost = (self.gesture, frame.ts)
            self.gesture = self.candidate = Gesture.NONE
            self.count = 0
            return GestureEvent(gesture=Gesture.NONE, ts=frame.ts)

        if self.lost:
            prev, lost_ts = self.lost
            self.lost = None
            if frame.ts - lost_ts <= LOST_GRACE_MS:
                self.gesture = self.candidate = prev  # 짧은 인식 끊김: 디바운스 없이 이어감
            else:
                self.fx.reset()
                self.fy.reset()

        lm = frame.landmarks
        g = self._debounce(classify(lm, drawing=self.gesture == Gesture.DRAW))
        pt = lm[MIDDLE_MCP] if g == Gesture.ERASE else lm[INDEX_TIP]
        x = 1 - pt[0] if frame.mirrored else pt[0]
        t = frame.ts / 1000.0

        def to_canvas_pt(p):
            px = 1 - p[0] if frame.mirrored else p[0]
            return (round(_to_canvas_x(px), 4), round(_to_canvas_y(p[1]), 4))

        cx = self.fx(_to_canvas_x(x), t)
        return GestureEvent(
            gesture=g,
            x=round(cx, 4),
            y=round(self.fy(_to_canvas_y(pt[1]), t), 4),
            ts=frame.ts,
            landmarks=[to_canvas_pt(p) for p in lm],
            hold=self._hold(),
            swipe=self._swipe(g, cx),
        )
