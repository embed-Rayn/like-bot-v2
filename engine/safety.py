"""차단 감지.

네이버가 제한을 걸 때 정확히 어떤 신호를 주는지는 확인되지 않았다. 그래서
안전은 간접 신호가 책임진다 — 네이버가 어떤 방식으로 막든 "갑자기 안 되기
시작함"은 공통이기 때문이다. 네이버의 구현을 몰라도 동작하고, 그 구현이
바뀌어도 계속 동작한다. 스펙 §7.2.
"""
from __future__ import annotations

from collections import deque

from engine.models import LikeOutcome

FAILURES = {
    LikeOutcome.TIMEOUT,
    LikeOutcome.ERROR,
    LikeOutcome.BLOCKED,
    LikeOutcome.NOT_LOGGED_IN,
}
SUCCESSES = {LikeOutcome.SUCCESS}
# ALREADY_LIKED / NO_BUTTON은 정상 상황이므로 어느 쪽에도 넣지 않는다.


class BlockDetector:
    def __init__(
        self,
        *,
        consecutive_failures: int = 5,
        window: int = 20,
        min_success_rate: float = 0.3,
    ) -> None:
        self._limit = consecutive_failures
        self._window_size = window
        self._min_rate = min_success_rate
        self._streak = 0
        self._window: deque[bool] = deque(maxlen=window)

    def record(self, outcome: LikeOutcome) -> str | None:
        """중단해야 하면 사유를, 아니면 None을 반환한다."""
        if outcome is LikeOutcome.BLOCKED:
            return "네이버가 제한을 건 것으로 보입니다 (직접 신호)."

        if outcome in FAILURES:
            self._streak += 1
            self._window.append(False)
        elif outcome in SUCCESSES:
            self._streak = 0
            self._window.append(True)
        else:
            return None    # 중립 — 연속 카운터도 창도 건드리지 않는다

        if self._streak >= self._limit:
            return f"{self._streak}건 연속 실패했습니다."

        if len(self._window) == self._window_size:
            rate = sum(self._window) / self._window_size
            if rate < self._min_rate:
                return f"최근 {self._window_size}건의 성공률이 {rate:.0%}로 떨어졌습니다."

        return None
