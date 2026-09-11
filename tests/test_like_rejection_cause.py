"""401을 받았을 때 원인을 가른다 — "다시 로그인하세요"가 소용없는 경우가 있다.

공감 API가 401을 돌려주면 지금까지는 무조건 `NOT_LOGGED_IN`이었고, 화면에는
"로그인 풀림 — 중단"이 떴다. 그런데 그 안내가 맞는 경우는 둘 중 하나뿐이다:

  · 우리 세션이 정말 죽었다        → 다시 로그인하면 해결된다
  · 세션은 멀쩡한데 공감만 거부됐다 → 다시 로그인해도 아무 소용이 없다

둘을 구분하지 못하면 운영자는 아무 효과도 없는 재로그인을 반복하게 된다.
2026-09-11~12에 실제로 그 고리에 갇혔다 — 세션은 blog.naver.com에서
살아 있다고 확인되는데(운영자 블로그로 정상 도착) 공감만 401이었다.

구분하는 방법은 이미 있다: `_is_logged_in()`이 쓰는 것과 같은 신호로
세션이 아직 살아 있는지 물어보면 된다. 살아 있는데도 401이면 로그인 문제가
아니다 — 계정 쪽 제한이고, 사람이 네이버에서 풀어야 한다.
"""
from __future__ import annotations

import pytest

from engine.like import (
    API_REJECT_SESSION_ALIVE,
    API_REJECT_SESSION_DEAD,
    classify_rejection,
)
from engine.models import LikeOutcome


def test_no_rejection_passes_through():
    """거부가 없었으면 호출자가 DOM으로 확인한다 — 여기서 판정하지 않는다."""
    assert classify_rejection(None, session_alive=True) is None
    assert classify_rejection(200, session_alive=True) is None


def test_401_with_a_dead_session_is_a_login_problem():
    outcome, detail = classify_rejection(401, session_alive=False)

    assert outcome is LikeOutcome.NOT_LOGGED_IN
    assert detail == API_REJECT_SESSION_DEAD
    assert "다시 로그인" in detail


def test_401_with_a_live_session_is_not_a_login_problem():
    """여기서 "로그인 풀림"이라고 말하면 운영자를 헛수고로 보낸다."""
    outcome, detail = classify_rejection(401, session_alive=True)

    assert outcome is not LikeOutcome.NOT_LOGGED_IN, (
        "세션이 살아 있는데도 로그인 문제라고 말합니다 — 재로그인은 소용없습니다."
    )
    assert outcome is LikeOutcome.BLOCKED
    assert detail == API_REJECT_SESSION_ALIVE
    assert "계정" in detail


@pytest.mark.parametrize("status", [403, 429])
def test_naver_saying_blocked_stays_blocked(status):
    """403/429는 네이버의 직접 차단 신호다 — 세션 상태와 무관하다."""
    for alive in (True, False):
        outcome, _ = classify_rejection(status, session_alive=alive)
        assert outcome is LikeOutcome.BLOCKED


def test_other_errors_are_not_relabelled_as_login_or_block():
    outcome, _ = classify_rejection(500, session_alive=True)
    assert outcome is LikeOutcome.ERROR


def test_both_details_are_distinguishable_on_screen():
    """두 문구가 같으면 로그를 봐도 어느 쪽이었는지 알 수 없다."""
    assert API_REJECT_SESSION_DEAD != API_REJECT_SESSION_ALIVE
