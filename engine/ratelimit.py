"""계정 단위 속도 제한.

워커 전원이 이 인스턴스 하나를 공유한다. 단일 이벤트 루프이므로 락이 필요 없다.

토큰 버킷이 아니라 간격 페이싱이다. 토큰 버킷은 유휴 시간에 토큰이 쌓여
버스트를 허용하는데, 계정 안전 관점에서 "한동안 쉬었으니 몰아서 누른다"는
정확히 피해야 할 패턴이다.

지터를 거는 이유: 정확히 일정한 간격은 그 규칙성 자체가 봇 신호다.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

BLOCK_PAUSE_MULTIPLIER = 1.8    # 블로그 전환 시 추가 휴식


class RateLimiter:
    def __init__(
        self,
        per_minute: float,
        *,
        jitter: tuple[float, float] = (0.6, 1.6),
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute은 0보다 커야 합니다.")
        self.base_interval = 60.0 / per_minute
        self._jitter = jitter
        self._clock = clock or time.monotonic
        self._sleep = sleeper or asyncio.sleep
        self._rng = rng or random.Random()
        self._next_allowed: float | None = None

    def _interval(self, multiplier: float = 1.0) -> float:
        return self.base_interval * multiplier * self._rng.uniform(*self._jitter)

    async def _wait_until_allowed(self, multiplier: float) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            await self._sleep(self._next_allowed - now)
            now = self._clock()
        # 유휴 시간이 길었더라도 다음 허용 시각은 '지금' 기준이다 (버스트 방지)
        self._next_allowed = now + self._interval(multiplier)

    async def acquire(self) -> None:
        """공감 1건에 대한 허가. 필요하면 대기한다."""
        await self._wait_until_allowed(1.0)

    async def pause_between_blogs(self) -> None:
        """블로그를 바꿀 때는 조금 더 쉰다 — 사람은 그렇게 움직인다."""
        await self._sleep(self._interval(BLOCK_PAUSE_MULTIPLIER))
        self._next_allowed = self._clock()
