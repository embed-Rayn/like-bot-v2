"""open()이 실패 시 브라우저/드라이버를 남기지 않는지 확인한다 (R8, R9).

캡차·2차인증·자격증명 오류는 정상 운영 중에도 자주 발생한다 — 세션을 저장해
재사용하는 이유 자체가 그 화면을 최대한 적게 통과하기 위해서다. 브라우저 기동
자체(바이너리 누락, 리소스 고갈)가 실패할 수도 있다. 어느 단계에서 실패하든
Chromium/Playwright 드라이버 프로세스가 하나씩 남으면 안 된다.

두 테스트 모두 실제 Playwright 드라이버(및 첫 번째 테스트는 실제 헤드리스
Chromium)를 기동하므로 로컬에 `playwright install chromium`이 되어 있어야
한다 — `browser` 마커로 표시해 기본 테스트 스위트(외부 바이너리에 의존하지
않아야 하는)에서는 제외한다.
"""
from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from engine.paths import AppPaths
from engine.session import BadCredentials, BrowserSession

pytestmark = pytest.mark.browser


async def test_open_closes_browser_and_playwright_when_login_fails(tmp_path, monkeypatch):
    """R8: 로그인 실패(캡차/2차인증/자격증명 등) 시 정리되는지 — 실제 브라우저까지
    정상적으로 기동한 뒤, 오직 `_is_logged_in`/`_login`만 흉내 낸다(계정/비밀번호
    없이는 실제 네이버 로그인 페이지를 테스트할 수 없으므로).

    close()는 이제 핸들을 None으로 리셋해 반복 호출에 안전하다(MINOR: idempotent
    close). 그래서 '실제로 브라우저를 기동했었는지'는 close() 직전에 핸들을
    가로채 확인하고, close() 이후에는 핸들이 실제로 리셋됐는지를 검증한다.
    """
    paths = AppPaths.for_app(tmp_path)
    session = BrowserSession(paths)

    async def fake_is_logged_in() -> bool:
        return False

    async def fake_login(account: str, **_kwargs) -> None:
        raise BadCredentials("forced failure for test")

    monkeypatch.setattr(session, "_is_logged_in", fake_is_logged_in)
    monkeypatch.setattr(session, "_login", fake_login)

    captured: dict[str, object] = {}
    real_close = session.close

    async def spy_close():
        captured["browser"] = session._browser
        captured["page"] = session.page
        await real_close()

    monkeypatch.setattr(session, "close", spy_close)

    with pytest.raises(BadCredentials):
        await session.open("test_account", headless=True)

    # open()이 스스로 만든 브라우저/드라이버가 실제로 정리됐는지 확인한다.
    assert captured["browser"] is not None, "테스트가 실제로 브라우저를 기동했는지 확인"
    assert captured["browser"].is_connected() is False
    assert captured["page"].is_closed() is True

    # close()가 핸들을 리셋해, 이 뒤에 또 close()를 불러도 안전하다.
    assert session._browser is None
    assert session._context is None
    assert session._pw is None
    assert session.page is None


async def test_open_stops_playwright_driver_when_browser_launch_fails(tmp_path, monkeypatch):
    """R9: 로그인 이전, 브라우저 기동 자체(`chromium.launch()`)가 실패해도
    Playwright 드라이버가 정리되는지. 실제 드라이버 프로세스를 기동한 뒤
    `chromium.launch`만 실패하도록 흉내 낸다 — Chromium 실행 자체는 필요 없다."""
    paths = AppPaths.for_app(tmp_path)
    session = BrowserSession(paths)

    real_pw = await async_playwright().start()
    stop_calls: list[bool] = []
    real_stop = real_pw.stop

    async def spy_stop() -> None:
        stop_calls.append(True)
        await real_stop()

    async def fake_launch(*args, **kwargs):
        raise RuntimeError("forced launch failure for test")

    monkeypatch.setattr(real_pw, "stop", spy_stop)
    monkeypatch.setattr(real_pw.chromium, "launch", fake_launch)

    class _FakeManager:
        async def start(self):
            return real_pw

    monkeypatch.setattr("engine.session.async_playwright", lambda: _FakeManager())

    with pytest.raises(RuntimeError):
        await session.open("test_account", headless=True)

    assert stop_calls, "브라우저 기동 실패 시 pw.stop()이 호출되지 않음 — 드라이버가 남는다"
    assert session._browser is None
    assert session._context is None
    assert session.page is None
