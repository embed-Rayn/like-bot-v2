"""기기 식별 쿠키를 버리지 않는다 — 2026-09-11 "자동 입력을 껐는데도 보호조치" 건.

자동 입력을 없앴는데도 네이버가 보호조치 화면을 띄웠다. 원인은 입력이 아니라
**우리가 매번 새 기기로 보이는 것**이었다.

저장된 세션(실측, 계정 wjdghldnjs9)은 쿠키 14개를 싣고 있고 그중 상당수는
인증이 아니라 기기·방문자 식별이다:

    NNB  NAC  NACT  BUC  nid_inf  nid_slevel  SRT5  SRT30 ...

`_discard_stale_session()`은 `clear_cookies()`를 인자 없이 불러 이 14개를 전부
지웠다. 그다음 열리는 로그인 창에는 네이버가 이 브라우저를 알아볼 단서가
하나도 없다 — 즉 **처음 보는 기기에서의 로그인**이고, 그것이 바로 추가 확인과
보호조치를 부르는 조건이다. 만료된 세션을 버리는 일과 기기 신원을 버리는 일은
전혀 다른 일인데 한 줄이 둘 다 해버렸다.

버릴 것은 만료된 인증 쿠키뿐이다.
"""
from __future__ import annotations

from engine.paths import AppPaths
from engine.session import AUTH_COOKIES, BrowserSession

# 실측된 이름 그대로 (2026-09-11, 저장된 storage_state에서 이름만 읽음)
STORED = [
    "NAC", "NACT", "NNB", "SRT30", "SRT5", "nid_slevel", "NID_JST",
    "BUC", "nid_inf", "NID_AUT", "NID_SES", "PM_CK_loc", "stat_yn", "JSESSIONID",
]


class _CookieJar:
    """clear_cookies(name=...)를 흉내 낸다 (playwright 1.62의 필터 인자)."""

    def __init__(self, names: list[str]) -> None:
        self.names = list(names)

    async def cookies(self) -> list[dict]:
        return [{"name": n} for n in self.names]

    async def clear_cookies(self, *, name: str | None = None, **_kw) -> None:
        if name is None:
            self.names = []
            return
        self.names = [n for n in self.names if n != name]


async def test_device_cookies_survive_a_stale_session():
    """NNB가 사라지면 다음 로그인은 '처음 보는 기기'가 된다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _CookieJar(STORED)

    await session._discard_stale_session()

    assert "NNB" in session._context.names, (
        "기기 식별 쿠키 NNB를 지웠습니다 — 네이버 눈에 새 기기로 보이고, "
        "그것이 보호조치를 부릅니다."
    )
    for name in ("NAC", "NACT", "BUC", "nid_inf"):
        assert name in session._context.names, f"{name}까지 지웠습니다."


async def test_expired_auth_cookies_are_actually_gone():
    """낡은 NID_AUT를 남기면 wait_for_manual_login이 사람이 손대기도 전에
    '로그인 완료'로 판정한다 — 버려야 할 것은 정확히 이것들이다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _CookieJar(STORED)

    await session._discard_stale_session()

    for name in AUTH_COOKIES:
        assert name not in session._context.names, (
            f"만료된 인증 쿠키 {name}이 남았습니다."
        )


async def test_it_does_not_wipe_the_whole_jar():
    session = BrowserSession(AppPaths.for_app())
    session._context = _CookieJar(STORED)

    await session._discard_stale_session()

    assert len(session._context.names) == len(STORED) - len(AUTH_COOKIES), (
        f"인증 쿠키만 지워야 하는데 {len(STORED) - len(session._context.names)}개를 "
        f"지웠습니다: 남은 것 {session._context.names}"
    )


# ---- 창을 사람에게서 빼앗지 않는다 (2026-09-11) ----
# "보호조치로 해제하니깐 꺼졌어" — 운영자가 본인확인을 하는 도중에 창이 닫혔다.
# MANUAL_LOGIN_TIMEOUT_S가 300초였기 때문이다. 휴대폰 본인확인이 들어가는
# 보호조치 해제는 5분 안에 끝나지 않는다. 그런데 타이머가 끝나면
# bootstrap_manual의 finally가 close()를 불러 창이 사라지고, 거기까지 한 일이
# 전부 날아간다.
#
# 대기의 끝은 타이머가 아니라 사람이어야 한다: 로그인이 끝나거나, 사람이 창을
# 닫거나. 타이머는 잊고 자리를 뜬 경우를 위한 뒷받침일 뿐이므로 넉넉해야 한다.

import engine.session as session_mod


class _ClosablePage:
    def __init__(self, *, closed: bool = False) -> None:
        self.url = "https://nid.naver.com/nidlogin.login"
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed

    async def goto(self, *_a, **_kw) -> None:
        raise AssertionError("대기 중에 페이지를 이동시켰습니다.")


class _NeverLogsIn:
    async def cookies(self) -> list[dict]:
        return [{"name": "NNB"}]


async def test_closing_the_window_ends_the_wait_immediately():
    """사람이 창을 닫으면 포기한 것이다 — 30분을 더 기다릴 이유가 없다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _NeverLogsIn()
    session.page = _ClosablePage(closed=True)

    assert await session.wait_for_manual_login(timeout_s=99999, poll_s=0.01) is False


async def test_the_wait_is_long_enough_to_clear_an_account_protection():
    """보호조치 해제(휴대폰 본인확인 등)는 5분 안에 끝나지 않는다."""
    assert session_mod.MANUAL_LOGIN_TIMEOUT_S >= 900, (
        f"{session_mod.MANUAL_LOGIN_TIMEOUT_S}초는 본인확인을 끝내기에 짧습니다 — "
        "운영자가 작업하는 도중에 창이 닫힙니다."
    )


async def test_the_operator_is_told_it_is_still_waiting(monkeypatch):
    """말없이 멈춰 있는 창 앞에서는 기다리는 중인지 죽은 것인지 알 수 없다."""
    async def instant(_seconds):
        pass

    monkeypatch.setattr("engine.session.asyncio.sleep", instant)
    monkeypatch.setattr("engine.session.MANUAL_LOGIN_NOTICE_S", 0)

    session = BrowserSession(AppPaths.for_app())
    session._context = _NeverLogsIn()
    session.page = _ClosablePage()
    seen: list[str] = []

    await session.wait_for_manual_login(timeout_s=0.05, poll_s=0.01, notify=seen.append)

    assert seen, "기다리는 동안 아무 말도 하지 않았습니다."
