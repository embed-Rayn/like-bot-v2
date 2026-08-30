"""open()이 로그인 실패 시 브라우저를 남기지 않는지 확인한다 (R8).

캡차·2차인증·자격증명 오류는 정상 운영 중에도 자주 발생한다 — 세션을 저장해
재사용하는 이유 자체가 그 화면을 최대한 적게 통과하기 위해서다. 그런 실패가
날 때마다 Chromium 프로세스가 하나씩 남으면 안 된다.

실제 로그인 페이지를 상대로 테스트할 수는 없으므로(계정·비밀번호 필요, 네이버
서버 의존) `_is_logged_in`/`_login`만 몬키패치해서 로그인 실패를 흉내 낸다.
Playwright 기동과 Chromium 실행 자체는 실제로 수행하여, close()가 실제로
핸들을 정리하는지(모킹된 close()가 아니라)를 검증한다.
"""
from __future__ import annotations

import pytest

from engine.paths import AppPaths
from engine.session import BadCredentials, BrowserSession


async def test_open_closes_browser_and_playwright_when_login_fails(tmp_path, monkeypatch):
    paths = AppPaths.for_app(tmp_path)
    session = BrowserSession(paths)

    async def fake_is_logged_in() -> bool:
        return False

    async def fake_login(account: str, password: str) -> None:
        raise BadCredentials("forced failure for test")

    monkeypatch.setattr(session, "_is_logged_in", fake_is_logged_in)
    monkeypatch.setattr(session, "_login", fake_login)

    with pytest.raises(BadCredentials):
        await session.open("test_account", lambda: "irrelevant", headless=True)

    # open()이 스스로 만든 브라우저/드라이버가 실제로 정리됐는지 확인한다.
    assert session._browser is not None, "테스트가 실제로 브라우저를 기동했는지 확인"
    assert session._browser.is_connected() is False
    assert session.page.is_closed() is True
