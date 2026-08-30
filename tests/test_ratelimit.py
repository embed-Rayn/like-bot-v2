import pytest

from engine.ratelimit import RateLimiter


class FakeClock:
    """가짜 시계 — 잠든 만큼 시간이 흐른다. 테스트가 결정론적이 된다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FixedRng:
    def __init__(self, value: float) -> None:
        self._value = value

    def uniform(self, a: float, b: float) -> float:
        return self._value


class SequenceRng:
    """호출마다 다른 지터 값을 순서대로 내어준다. 목록이 바닥나면 즉시 실패한다 —
    draw 횟수가 바뀌는 회귀도 함께 잡아낸다."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def uniform(self, a: float, b: float) -> float:
        if not self._values:
            raise AssertionError("SequenceRng exhausted: unexpected extra uniform() call")
        return self._values.pop(0)


def _limiter(per_minute=6.0, jitter_value=1.0):
    clock = FakeClock()
    limiter = RateLimiter(
        per_minute,
        clock=clock.time,
        sleeper=clock.sleep,
        rng=FixedRng(jitter_value),
    )
    return limiter, clock


def test_base_interval_derives_from_per_minute():
    limiter, _ = _limiter(per_minute=6.0)
    assert limiter.base_interval == pytest.approx(10.0)


async def test_first_acquire_does_not_sleep():
    limiter, clock = _limiter()
    await limiter.acquire()
    assert clock.slept == []


async def test_second_acquire_waits_the_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.0)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.slept == [pytest.approx(10.0)]


async def test_jitter_scales_the_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.5)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.slept == [pytest.approx(15.0)]


async def test_no_burst_after_idle_time():
    """유휴 시간이 길어도 다음 호출은 여전히 즉시 1회만 허용된다."""
    limiter, clock = _limiter(per_minute=6.0)
    await limiter.acquire()
    clock.now += 600           # 10분 유휴
    await limiter.acquire()    # 밀린 만큼 몰아치지 않는다
    await limiter.acquire()
    assert clock.slept == [pytest.approx(10.0)]


async def test_pause_between_blogs_is_longer_than_one_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.0)
    await limiter.pause_between_blogs()
    assert clock.slept and clock.slept[0] > limiter.base_interval


async def test_acquire_after_pause_does_not_wait_again():
    """pause_between_blogs가 _next_allowed를 '지금'으로 초기화하지 않으면, 첫 acquire()가
    남겨둔 미래의 예약 시각이 그대로 남아 있다가 pause 이후의 acquire()를 불필요하게
    한 번 더 재운다. FixedRng로는 이 차이를 드러낼 수 없다 — pause의 배율(1.8)이
    항상 이전 acquire의 배율(1.0)을 압도해 스케줄이 절대 앞서지 못하기 때문이다.
    그래서 호출마다 다른 지터를 주는 SequenceRng로 '먼저 큰 지터로 예약을 멀리
    밀어둔 뒤, pause에는 작은 지터를 주는' 상황을 만든다.

    세 번째 draw(1.0)는 세 번째 acquire()가 항상 계산하는 '다음' 예약(_next_allowed
    갱신)을 위한 것이며 clock.slept 단언과는 무관하다 — _wait_until_allowed는 잠들지
    않았을 때도 다음 예약 시각을 새로 계산하므로, SequenceRng가 바닥나지 않으려면
    반드시 필요하다."""
    clock = FakeClock()
    limiter = RateLimiter(
        6.0,  # base_interval == 10.0
        clock=clock.time,
        sleeper=clock.sleep,
        rng=SequenceRng([1.6, 0.6, 1.0]),
    )

    await limiter.acquire()  # jitter 1.6 -> 안 잔다; _next_allowed = 0 + 10*1.0*1.6 = 16
    await limiter.pause_between_blogs()  # jitter 0.6 -> 10*1.8*0.6 = 10.8초 잔다

    await limiter.acquire()  # 리셋이 없다면 _next_allowed(16)가 아직 남아 있어 5.2초 더 잔다

    assert clock.slept == [pytest.approx(10.8)]


def test_invalid_rate_rejected():
    with pytest.raises(ValueError):
        RateLimiter(0)
