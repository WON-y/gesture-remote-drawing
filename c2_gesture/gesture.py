"""랜드마크 → 제스처 판정 + 좌표 스무딩(One Euro Filter).

MediaPipe Hand Landmarker 인덱스:
  0 손목 / 2 엄지 MCP, 4 엄지 끝 / 5·9·13·17 각 손가락 MCP / 6·10·14·18 PIP / 8·12·16·20 끝(TIP)
"""
import math
import os

from common.schema import Gesture, GestureEvent, LandmarkFrame

WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_MCP, INDEX_TIP, MIDDLE_MCP = 5, 8, 9
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]  # (tip, pip): 검지, 중지, 약지, 새끼

EXTEND_RATIO = float(os.getenv("EXTEND_RATIO", "1.1"))   # tip-손목 거리 / pip-손목 거리
# 그리는 중(draw)에는 판정을 느슨하게: 검지는 덜 펴져도, 다른 손가락은 더 확실히 펴져야 인정
# 검지는 PIP 대신 뿌리(5) 기준(tip-손목 / mcp-손목): PIP 에서 굽어도 1.4 이상, 주먹으로 말면 1.0 미만
DRAW_INDEX_RATIO = float(os.getenv("DRAW_INDEX_RATIO", "1.2"))
DRAW_OTHER_RATIO = float(os.getenv("DRAW_OTHER_RATIO", "1.25"))
STABLE_FRAMES = int(os.getenv("STABLE_FRAMES", "3"))     # 제스처 전환에 필요한 연속 프레임
DRAW_RELEASE_FRAMES = int(os.getenv("DRAW_RELEASE_FRAMES", "6"))  # draw 에서 빠져나갈 때 필요한 연속 프레임
LOST_GRACE_MS = float(os.getenv("LOST_GRACE_MS", "300"))  # 손을 잠깐 놓쳤다 찾으면 이전 제스처 이어가기
SAVE_FRAMES = int(os.getenv("SAVE_FRAMES", "15"))       # 엄지 척 유지 프레임(오작동 방지로 더 길게)
THUMB_MARGIN = float(os.getenv("THUMB_MARGIN", "0.2"))  # 엄지 끝이 나머지보다 높아야 하는 정도(손 크기 비율)
# 핀치(지우개): 엄지 끝-검지 끝 거리가 손 크기 × 이 비율보다 작으면 인정.
# 지우는 중에는 더 넓은 RELEASE 비율을 써서 경계에서 erase ↔ 다른 제스처가 깜빡이지 않게 함
PINCH_RATIO = float(os.getenv("PINCH_RATIO", "0.3"))
PINCH_RELEASE_RATIO = float(os.getenv("PINCH_RELEASE_RATIO", "0.45"))
ACTIVE_MARGIN = float(os.getenv("ACTIVE_MARGIN", "0.1"))  # 카메라 좌우 여백(0~0.5)
ACTIVE_TOP = float(os.getenv("ACTIVE_TOP", "0.05"))       # 카메라 위 여백
# 카메라 아래 여백. 검지 끝은 손 맨 위라 끝까지 내리면 손바닥이 화면 밖으로 나가 인식이 끊기므로 크게 둠
ACTIVE_BOTTOM = float(os.getenv("ACTIVE_BOTTOM", "0.4"))
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


def pinch(lm, ratio: float = PINCH_RATIO) -> bool:
    """엄지 끝(4)과 검지 끝(8)이 맞닿음. 주먹에서도 두 끝이 가까울 수 있어서
    검지 끝이 검지 뿌리(5)보다 손목에서 멀 때(손바닥 안으로 말려 있지 않을 때)만 인정."""
    size = _dist(lm[WRIST], lm[MIDDLE_MCP])
    near = _dist(lm[THUMB_TIP], lm[INDEX_TIP]) < ratio * size
    return near and _dist(lm[INDEX_TIP], lm[WRIST]) > _dist(lm[INDEX_MCP], lm[WRIST])


def classify(lm, drawing: bool = False, erasing: bool = False) -> Gesture:
    # 핀치는 다른 손가락 모양과 상관없이 먼저 판정 (새끼 등을 신경 쓰지 않아도 되도록)
    if pinch(lm, PINCH_RELEASE_RATIO if erasing else PINCH_RATIO):
        return Gesture.ERASE
    ratios = (EXTEND_RATIO,) + (DRAW_OTHER_RATIO,) * 3 if drawing else (EXTEND_RATIO,) * 4
    i, m, r, p = finger_states(lm, ratios)
    if drawing:  # 그리다 검지가 굽어도(끝이 PIP 와 겹쳐 보여도) 손바닥 안으로 말리지 않았으면 유지
        i = _dist(lm[INDEX_TIP], lm[WRIST]) > _dist(lm[INDEX_MCP], lm[WRIST]) * DRAW_INDEX_RATIO
    if i and not (m or r or p):
        return Gesture.DRAW
    if i and m and not (r or p):
        return Gesture.HOVER
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
        self.fx, self.fy = OneEuroFilter(), OneEuroFilter()

    def _debounce(self, g: Gesture) -> Gesture:
        if g == self.gesture:
            self.candidate, self.count = g, 0
        elif g == self.candidate:
            self.count += 1
            if g == Gesture.SAVE:
                need = SAVE_FRAMES
            elif self.gesture == Gesture.DRAW:
                need = DRAW_RELEASE_FRAMES
            else:
                need = STABLE_FRAMES
            if self.count >= need:
                self.gesture, self.count = g, 0
        else:
            self.candidate, self.count = g, 1
        return self.gesture

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
        g = self._debounce(classify(lm, drawing=self.gesture == Gesture.DRAW,
                                    erasing=self.gesture == Gesture.ERASE))
        if g == Gesture.ERASE:  # 지우개는 엄지와 검지가 맞닿은 점
            a, b = lm[THUMB_TIP], lm[INDEX_TIP]
            pt = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        else:
            pt = lm[INDEX_TIP]
        x = 1 - pt[0] if frame.mirrored else pt[0]
        t = frame.ts / 1000.0

        def to_canvas_pt(p):
            px = 1 - p[0] if frame.mirrored else p[0]
            return (round(_to_canvas_x(px), 4), round(_to_canvas_y(p[1]), 4))

        return GestureEvent(
            gesture=g,
            x=round(self.fx(_to_canvas_x(x), t), 4),
            y=round(self.fy(_to_canvas_y(pt[1]), t), 4),
            ts=frame.ts,
            landmarks=[to_canvas_pt(p) for p in lm],
        )
