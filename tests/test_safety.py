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
