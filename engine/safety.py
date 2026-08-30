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
# ALREADY_LIKED / NO_BUTTON은 정상 상황이므로 연속 실패 카운터와 성공률
# 창 어느 쪽에도 넣지 않는다.


class BlockDetector:
    def __init__(
        self,
        *,
        consecutive_failures: int = 5,
        window: int = 20,
        min_success_rate: float = 0.3,
        no_button_streak: int = 15,
    ) -> None:
        self._limit = consecutive_failures
        self._window_size = window
        self._min_rate = min_success_rate
        self._no_button_limit = no_button_streak
        self._streak = 0
        self._window: deque[bool] = deque(maxlen=window)
        # I1: NO_BUTTON은 개별적으로는 정상(비공개 · 삭제 · 공감 비허용)이라
        # 연속 실패 카운터도 성공률 창도 건드리지 않는다. 그런데 네이버가
        # 글은 계속 보여주면서 공감 버튼만 숨기거나 제거하는 방식으로 막을
        # 수도 있다 — 그러면 결과는 매번 NO_BUTTON뿐이라 두 안전장치 모두
        # 침묵한 채 수백 번을 그냥 돈다. 한 번의 NO_BUTTON은 정상이지만
        # 끊이지 않는 연속은 아니므로, 이것만을 위한 별도 연속 카운터를 둔다.
        self._no_button_streak = 0

    def record(self, outcome: LikeOutcome) -> str | None:
        """중단해야 하면 사유를, 아니면 None을 반환한다."""
        if outcome is LikeOutcome.BLOCKED:
            return "네이버가 제한을 건 것으로 보입니다 (직접 신호)."

        if outcome is LikeOutcome.NO_BUTTON:
            self._no_button_streak += 1
            if self._no_button_streak >= self._no_button_limit:
                return (
                    f"공감 버튼을 {self._no_button_streak}건 연속 찾지 "
                    "못했습니다 (간접 신호)."
                )
            return None    # 개별로는 정상 — 연속 카운터도 창도 건드리지 않는다

        if outcome is LikeOutcome.ALREADY_LIKED:
            # 페이지와 버튼 자체는 여전히 닿는다는 뜻이므로 NO_BUTTON 연속을
            # 끊는다. 연속 실패 카운터 · 성공률 창은 그대로 건드리지 않는다.
            self._no_button_streak = 0
            return None

        if outcome in FAILURES:
            self._streak += 1
            self._window.append(False)
        elif outcome in SUCCESSES:
            self._streak = 0
            self._no_button_streak = 0
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
