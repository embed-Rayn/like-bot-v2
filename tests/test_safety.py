from engine.models import LikeOutcome as O
from engine.safety import BlockDetector


def test_blocked_outcome_trips_immediately():
    d = BlockDetector()
    assert d.record(O.BLOCKED) is not None


def test_successes_never_trip():
    d = BlockDetector()
    for _ in range(100):
        assert d.record(O.SUCCESS) is None


def test_consecutive_failures_trip():
    d = BlockDetector(consecutive_failures=3)
    assert d.record(O.ERROR) is None
    assert d.record(O.TIMEOUT) is None
    assert d.record(O.ERROR) is not None


def test_a_success_resets_the_consecutive_counter():
    d = BlockDetector(consecutive_failures=3)
    d.record(O.ERROR)
    d.record(O.ERROR)
    d.record(O.SUCCESS)
    assert d.record(O.ERROR) is None


def test_neutral_outcomes_do_not_count_as_failures():
    """이미 공감했거나 버튼이 없는 것은 정상 상황이다."""
    d = BlockDetector(consecutive_failures=3)
    for _ in range(50):
        assert d.record(O.ALREADY_LIKED) is None
        assert d.record(O.NO_BUTTON) is None


def test_neutral_outcomes_do_not_reset_the_consecutive_counter():
    d = BlockDetector(consecutive_failures=3)
    d.record(O.ERROR)
    d.record(O.ALREADY_LIKED)
    d.record(O.ERROR)
    assert d.record(O.ERROR) is not None


def test_success_rate_only_evaluated_once_window_is_full():
    d = BlockDetector(consecutive_failures=99, window=10, min_success_rate=0.5)
    # 실패 4 + 성공 5 = 9건, 창이 아직 안 참
    for _ in range(4):
        assert d.record(O.ERROR) is None
    for _ in range(5):
        assert d.record(O.SUCCESS) is None


def test_low_success_rate_over_full_window_trips():
    d = BlockDetector(consecutive_failures=99, window=10, min_success_rate=0.5)
    tripped = None
    for i in range(10):
        tripped = d.record(O.SUCCESS if i < 3 else O.ERROR)
    assert tripped is not None


def test_reason_names_the_signal():
    d = BlockDetector(consecutive_failures=2)
    d.record(O.ERROR)
    assert "연속" in d.record(O.ERROR)


# ---- I1: NO_BUTTON 연속 스트릭도 안전망이 있어야 한다 ----


def test_short_no_button_streak_does_not_trip():
    d = BlockDetector(no_button_streak=15)
    for _ in range(14):
        assert d.record(O.NO_BUTTON) is None


def test_no_button_streak_trips_at_threshold():
    d = BlockDetector(no_button_streak=5)
    for _ in range(4):
        assert d.record(O.NO_BUTTON) is None
    reason = d.record(O.NO_BUTTON)
    assert reason is not None
    assert "버튼" in reason


def test_already_liked_resets_the_no_button_streak():
    d = BlockDetector(no_button_streak=3)
    d.record(O.NO_BUTTON)
    d.record(O.NO_BUTTON)
    d.record(O.ALREADY_LIKED)          # 페이지 · 버튼이 여전히 닿는다는 신호
    assert d.record(O.NO_BUTTON) is None
    assert d.record(O.NO_BUTTON) is None    # 리셋 후 2건째 — 아직 3에 못 미침


def test_success_resets_the_no_button_streak():
    d = BlockDetector(no_button_streak=3)
    d.record(O.NO_BUTTON)
    d.record(O.NO_BUTTON)
    d.record(O.SUCCESS)
    assert d.record(O.NO_BUTTON) is None
    assert d.record(O.NO_BUTTON) is None


def test_no_button_streak_does_not_affect_consecutive_failure_counter():
    """NO_BUTTON 자체는 여전히 실패로 카운트되지 않는다."""
    d = BlockDetector(consecutive_failures=3, no_button_streak=99)
    d.record(O.ERROR)
    d.record(O.NO_BUTTON)
    d.record(O.NO_BUTTON)
    assert d.record(O.ERROR) is None       # 아직 연속 실패 2건일 뿐


def test_default_consecutive_limit_is_the_operator_chosen_1000():
    """운영자 결정 (2026-09-07): 연속 실패만으로는 사실상 멈추지 않는다.
    기본값이 조용히 5로 되돌아가면 그 결정이 사라지므로 못 박아 둔다."""
    # 성공률 창(기본 20)이 먼저 걸리지 않도록 넓혀 두고 연속 상한만 본다.
    d = BlockDetector(window=2000)
    for _ in range(999):
        assert d.record(O.ERROR) is None
    assert d.record(O.ERROR) is not None      # 1000번째에 비로소 걸린다


def test_the_success_rate_window_is_what_actually_stops_a_failing_run():
    """연속 상한을 1000으로 올려도 성공률 창은 그대로 살아 있다 — 계속
    실패하는 실행은 20건 남짓에서 멈춘다. 이것이 남은 간접 신호다."""
    d = BlockDetector()
    tripped = None
    for _ in range(20):
        tripped = d.record(O.ERROR)
    assert tripped is not None
    assert "성공률" in tripped
