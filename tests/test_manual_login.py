"""수동 로그인 부트스트랩 — 사람이 직접 로그인하고, 세션만 저장한다.

네이버는 자동 입력 로그인에 "보안을 위해 추가 확인" 화면을 띄운다
(2026-08-31 관측). 그 화면은 사람이 풀어야 하므로, 최초 1회는 운영자가 창에서
직접 로그인하고 그 세션을 저장해 이후 실행이 로그인 페이지를 거치지 않게 한다
— 설계 문서가 storage_state 재사용을 둔 이유 그대로다.

브라우저를 띄우지 않는다. 대기 · 저장 규칙만 가짜 컨텍스트로 확인한다.
"""
from __future__ import annotations

import pytest

from engine.paths import AppPaths
from engine.session import BrowserSession


class _FakeContext:
    """호출 횟수에 따라 로그인 쿠키가 생기는 컨텍스트."""

    def __init__(self, appears_at: int | None) -> None:
        self._appears_at = appears_at
        self.calls = 0

    async def cookies(self) -> list[dict]:
        self.calls += 1
        if self._appears_at is not None and self.calls >= self._appears_at:
            return [{"name": "NID_AUT", "value": "x"}]
        return [{"name": "NNB", "value": "y"}]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def instant(_seconds):
        pass

    monkeypatch.setattr("engine.session.asyncio.sleep", instant)


async def test_wait_returns_true_once_the_login_cookie_appears():
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeContext(appears_at=3)

    assert await session.wait_for_manual_login(timeout_s=60, poll_s=0.01) is True


async def test_wait_gives_up_after_the_timeout():
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeContext(appears_at=None)

    assert await session.wait_for_manual_login(timeout_s=0, poll_s=0.01) is False


async def test_wait_does_not_navigate_the_page_the_operator_is_typing_into():
    """페이지를 이동시키면 운영자가 입력 중인 로그인 화면이 날아간다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeContext(appears_at=1)

    class _PageThatMustNotMove:
        async def goto(self, *_a, **_kw):
            raise AssertionError("대기 중에 페이지를 이동시켰습니다.")

    session.page = _PageThatMustNotMove()

    assert await session.wait_for_manual_login(timeout_s=60, poll_s=0.01) is True


async def test_bootstrap_never_types_a_password(monkeypatch, tmp_path):
    """부트스트랩은 자동 로그인을 시도하지 않는다 — 그래서 추가 확인을 부르지 않는다."""
    session = BrowserSession(AppPaths.for_app(tmp_path))
    saved: list[str] = []
    logged = {"value": False}

    async def fake_start(_state_file, headless=False):
        session._context = _FakeContext(appears_at=1)

    async def fake_login(*_a, **_kw):
        raise AssertionError("자동 로그인을 시도했습니다.")

    async def is_logged_in():
        return logged["value"]

    async def save_state(state_file):
        saved.append(str(state_file))

    async def close():
        pass

    class _Page:
        async def goto(self, *_a, **_kw):
            logged["value"] = True    # 사람이 로그인을 끝낸 상황을 흉내 낸다

    monkeypatch.setattr(session, "_start", fake_start)
    monkeypatch.setattr(session, "_login", fake_login)
    monkeypatch.setattr(session, "_is_logged_in", is_logged_in)
    monkeypatch.setattr(session, "_save_state", save_state)
    monkeypatch.setattr(session, "close", close)
    session.page = _Page()

    ok = await session.bootstrap_manual("someid", timeout_s=60)

    assert ok is True
    assert saved, "세션을 저장하지 않았습니다 — 다음 실행이 또 로그인 페이지를 거칩니다."
