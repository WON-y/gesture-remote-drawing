"""c2_gesture 판정 로직 단위 테스트. 실제 카메라 대신 합성 랜드마크를 사용한다.

합성 손: 손목(0)이 아래, 손가락이 위(이미지 y 작을수록 위)를 향한 정면 손.
"""
import pytest

from common.schema import Gesture, LandmarkFrame
from c2_gesture import gesture as gm
from c2_gesture.gesture import GestureEngine, classify, fingers_together, pinch

FRAME_MS = 33  # 약 30fps
V = (1, 1, 0, 0)  # 검지+중지


def hand(ext=(0, 0, 0, 0), dx=0.0, together=False, thumb_up=False, pinching=False):
    """ext = (검지, 중지, 약지, 새끼) 펴짐 여부. dx = 손 전체 좌우 이동."""
    lm = [[0.5, 0.9, 0.0] for _ in range(21)]
    for k, (tip, pip) in enumerate(gm.FINGERS):
        x = 0.4 + 0.07 * k
        lm[tip - 3] = [x, 0.7, 0.0]  # MCP
        lm[pip] = [x, 0.6, 0.0]
        lm[tip - 1] = [x, 0.5 if ext[k] else 0.68, 0.0]
        lm[tip] = [x, 0.4 if ext[k] else 0.75, 0.0]
    if together:  # 중지 끝을 검지 끝 옆으로 붙임
        lm[12][0] = lm[11][0] = 0.42
    elif ext[0] and ext[1]:  # ✌ 처럼 벌림
        lm[12][0] = lm[11][0] = 0.55
    for j in range(1, 5):  # 엄지: 손바닥 옆에 접힘
        lm[j] = [0.36, 0.85 - 0.02 * j, 0.0]
    if thumb_up:
        lm[2], lm[3], lm[4] = [0.36, 0.7, 0.0], [0.36, 0.55, 0.0], [0.36, 0.4, 0.0]
    if pinching:  # 엄지 끝을 검지 끝에 맞댐
        lm[4] = [lm[8][0] + 0.01, lm[8][1] + 0.01, 0.0]
    return [(p[0] + dx, p[1], p[2]) for p in lm]


class Feeder:
    """GestureEngine 에 프레임을 순서대로 넣는 도우미."""

    def __init__(self):
        self.engine = GestureEngine()
        self.ts = 0.0

    def __call__(self, lm, n=1):
        ev = None
        for _ in range(n):
            self.ts += FRAME_MS
            ev = self.engine.update(LandmarkFrame(ts=self.ts, mirrored=False, landmarks=lm))
        return ev


def triangle(i, amp=0.04, period=8):
    """좌우 흔들기용 삼각파 (-amp ~ amp)."""
    return amp * (1 - abs((i % period) - period / 2) / (period / 4))


# ---- 손 모양 판정 ----
@pytest.mark.parametrize("lm, expected", [
    (hand((1, 0, 0, 0)), Gesture.DRAW),
    (hand(V), Gesture.HOVER),
    (hand((0, 0, 0, 0)), Gesture.IDLE),
    (hand((0, 0, 0, 0), thumb_up=True), Gesture.SAVE),
    (hand((1, 0, 0, 1)), Gesture.CLEAR),
    (hand((1, 1, 1, 1), pinching=True), Gesture.ERASE),
    (hand((1, 1, 1, 0)), Gesture.HOVER),  # 애매한 손 모양은 커서 이동
    (hand((1, 1, 1, 1)), Gesture.HOVER),
])
def test_classify(lm, expected):
    assert classify(lm) == expected


def test_pinch_ignores_fist():
    # 주먹에서 엄지 끝이 검지 근처에 있어도 검지가 말려 있으면 핀치 아님
    assert not pinch(hand((0, 0, 0, 0), pinching=True))


def test_fingers_together():
    assert fingers_together(hand(V, together=True))
    assert not fingers_together(hand(V))


# ---- 디바운스 ----
def test_debounce_needs_stable_frames():
    f = Feeder()
    f(hand(V), n=gm.STABLE_FRAMES + 1)
    assert f(hand((1, 0, 0, 0)), n=gm.STABLE_FRAMES - 1).gesture == Gesture.HOVER
    assert f(hand((1, 0, 0, 0))).gesture == Gesture.DRAW


def test_clear_needs_long_hold_and_reports_progress():
    f = Feeder()
    f(hand((0, 0, 0, 0)), n=gm.STABLE_FRAMES + 1)
    holds = [f(hand((1, 0, 0, 1))).hold for _ in range(gm.CLEAR_FRAMES - 1)]
    assert f.engine.gesture == Gesture.IDLE
    assert holds == sorted(holds) and 0 < holds[0] < holds[-1] < 1
    ev = f(hand((1, 0, 0, 1)))
    assert ev.gesture == Gesture.CLEAR and ev.hold is None


def test_hand_lost_sends_none_once():
    f = Feeder()
    f(hand((1, 0, 0, 0)), n=gm.STABLE_FRAMES + 1)
    assert f([]).gesture == Gesture.NONE
    assert f([]) is None


# ---- 색상 창: 붙이고 흔들기 → 스와이프 ----
def test_spread_v_shake_does_not_open_palette():
    f = Feeder()
    for i in range(30):
        assert f(hand(V, dx=triangle(i))).gesture != Gesture.PALETTE


def test_together_without_shake_is_hover():
    f = Feeder()
    assert f(hand(V, together=True), n=30).gesture == Gesture.HOVER


def test_shake_opens_palette_then_swipe_and_release():
    f = Feeder()
    f(hand(V, together=True), n=gm.STABLE_FRAMES + 1)

    events = [f(hand(V, dx=triangle(i), together=True)) for i in range(30)]
    opened = next(i for i, ev in enumerate(events) if ev.gesture == Gesture.PALETTE)
    assert any(ev.hold for ev in events[:opened])  # 흔드는 동안 진행률 표시
    assert not any(ev.swipe for ev in events)  # 흔들기만으로는 색이 넘어가지 않음

    # 카메라 기준 0.39 이동 = 그림판 기준 약 0.49 (좌우 여백 확장) → SWIPE_DIST 마다 한 칸
    right = [s for s in (f(hand(V, dx=i * 0.01, together=True)).swipe for i in range(40)) if s]
    assert len(right) >= 3 and set(right) == {1}
    left = [s for s in (f(hand(V, dx=0.39 - i * 0.01, together=True)).swipe for i in range(40)) if s]
    assert len(left) >= 3 and set(left) == {-1}

    assert f(hand(V, dx=0.0)).gesture == Gesture.HOVER  # 손가락을 떼면 닫힘
    assert not f.engine.palette
