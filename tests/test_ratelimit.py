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
    """pause_between_blogs 직후에는 _next_allowed가 '지금'으로 초기화되므로,
    이어지는 acquire()는 다시 잠들지 않는다."""
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.0)
    await limiter.pause_between_blogs()
    slept_after_pause = len(clock.slept)
    await limiter.acquire()
    assert len(clock.slept) == slept_after_pause


def test_invalid_rate_rejected():
    with pytest.raises(ValueError):
        RateLimiter(0)
