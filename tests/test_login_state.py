"""로그인 여부를 무엇으로 판정하는가 — 2026-09-11 사고의 재발 방지 테스트.

사고: `_is_logged_in()`이 로그인 페이지(`nidlogin.login?mode=form`)를 열고
"아직 nid.naver.com인가"로 판정했다. 이 질문에는 답이 없다 — 로그인 폼이
보인다는 사실은 로그인 상태에서도, 로그아웃 상태에서도 일어날 수 있다.
실제 결과는 두 겹이었다:

  1. 멀쩡한 세션을 로그아웃으로 판정해 `_discard_stale_session()`이 지웠고,
     매 실행이 자동 비밀번호 입력으로 갔다 → 네이버의 계정 보호 조치.
  2. 사람이 손으로 로그인에 성공한 직후에도 같은 판정이 False라서
     `SessionExpired("로그인 직후 세션이 확인되지 않았습니다")`로 터졌다.
     실행 로그 run-20260911-231421이 그 모습이다 — 챌린지 안내조차 없이 끝났다.

판정은 **로그인해야만 볼 수 있는 페이지**에게 물어야 한다. 로그아웃이면
네이버가 서버측 302로 nid 로그인 폼에 데려다 놓는다 (실측 2026-09-11,
로그아웃 컨텍스트에서 blog.naver.com/MyBlog.naver → nid.naver.com).
"""
from __future__ import annotations

from engine.paths import AppPaths
from engine.session import LOGIN_HOST, LOGIN_URL, BrowserSession


class _Cookies:
    def __init__(self, *, logged_cookie: bool) -> None:
        self._logged_cookie = logged_cookie
        self.cleared = False

    async def cookies(self) -> list[dict]:
        if self._logged_cookie:
            return [{"name": "NID_AUT", "value": "x"}, {"name": "NNB", "value": "y"}]
        return [{"name": "NNB", "value": "y"}]

    async def clear_cookies(self, *, name: str | None = None, **_kw) -> None:
        self.cleared = True


class _Page:
    """네이버를 흉내 낸다: 로그인 필수 페이지는 로그아웃일 때만 nid로 튕긴다.

    로그인 페이지 자신(`nidlogin.login`)은 **어느 쪽이든 그 자리에 머문다** —
    이것이 그 주소를 근거로 쓸 수 없는 이유다.
    """

    def __init__(self, *, session_alive: bool) -> None:
        self._alive = session_alive
        self.url = "about:blank"
        self.visited: list[str] = []

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, **_kw) -> None:
        self.visited.append(url)
        if LOGIN_HOST in url:
            self.url = url                      # 로그인 페이지는 늘 로그인 페이지다
        elif self._alive:
            self.url = "https://blog.naver.com/wjdghldnjs9"
        else:
            self.url = f"https://{LOGIN_HOST}/nidlogin.login?mode=form&url={url}"


def _session(*, logged_cookie: bool, session_alive: bool) -> BrowserSession:
    session = BrowserSession(AppPaths.for_app())
    session._context = _Cookies(logged_cookie=logged_cookie)
    session.page = _Page(session_alive=session_alive)
    return session


async def test_live_session_is_recognized_as_logged_in():
    """살아 있는 세션을 로그아웃으로 판정하면 멀쩡한 세션을 버리고 다시 로그인한다."""
    session = _session(logged_cookie=True, session_alive=True)

    assert await session._is_logged_in() is True


async def test_the_login_page_is_never_used_as_the_oracle():
    session = _session(logged_cookie=True, session_alive=True)

    await session._is_logged_in()

    assert not any(LOGIN_HOST in url for url in session.page.visited), (
        "로그인 페이지를 로그인 여부의 근거로 썼습니다. 그 페이지가 로그인 폼을 "
        f"보여준다는 사실은 어느 쪽도 증명하지 못합니다: {session.page.visited}"
    )
    assert LOGIN_URL not in session.page.visited


async def test_expired_session_is_recognized_as_logged_out():
    """쿠키는 남아 있지만 네이버가 세션을 끊은 상태 — 2026-09-10 사고의 모양."""
    session = _session(logged_cookie=True, session_alive=False)

    assert await session._is_logged_in() is False


async def test_probe_page_requires_login_so_a_bounce_means_logged_out():
    """프로브 대상은 '로그인해야만 볼 수 있는 페이지'여야 한다."""
    session = _session(logged_cookie=True, session_alive=False)

    await session._is_logged_in()

    assert session.page.visited, "아무 페이지도 열어 보지 않고 판정했습니다."
    assert all(LOGIN_HOST not in url for url in session.page.visited)


async def test_no_cookie_skips_the_network_entirely():
    """로그인 페이지는 가장 방어가 심한 화면이다 — 갈 이유가 없으면 가지 않는다 (결정 3)."""
    session = _session(logged_cookie=False, session_alive=False)

    assert await session._is_logged_in() is False
    assert session.page.visited == [], (
        f"쿠키가 없는데도 네트워크를 탔습니다: {session.page.visited}"
    )


# ---- 자동 입력 폐지 (운영자 결정, 2026-09-11) ----
# 자동 입력은 네이버의 "보안을 위해 추가 확인" 화면을 부르고(2026-08-31 실측),
# 그 화면은 어차피 사람이 푼다. 계정에 남는 것은 실패한 자동 로그인 시도뿐이고
# 그것이 쌓이면 보호 조치가 된다 — 2026-09-11 실행 로그가 3분 간격 연속 시도를
# 보여 준다. 그래서 창만 열고 사람을 기다린다.


class _FormPage:
    """자판을 두드리거나 버튼을 누르면 즉시 실패하는 로그인 화면."""

    def __init__(self) -> None:
        self.url = LOGIN_URL
        self.visited: list[str] = []

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, **_kw) -> None:
        self.visited.append(url)
        self.url = url

    async def inner_text(self, _selector: str) -> str:
        return "네이버 아이디 또는 전화번호 비밀번호 로그인 일회용 번호 로그인"

    async def click(self, selector: str) -> None:
        raise AssertionError(f"로그인 폼을 눌렀습니다: {selector}")

    def locator(self, selector: str):
        raise AssertionError(f"로그인 폼을 찾았습니다: {selector}")

    @property
    def keyboard(self):
        raise AssertionError("로그인 창에 자판 입력을 흘렸습니다.")


async def test_login_never_types_credentials():
    session = BrowserSession(AppPaths.for_app())
    session._context = _Cookies(logged_cookie=True)
    session.page = _FormPage()
    messages: list[str] = []

    async def logged_in() -> bool:
        return True

    session._is_logged_in = logged_in

    await session._login("wjdghldnjs9", on_challenge=messages.append)

    assert messages, "무엇을 해야 하는지 알리지 않고 창 앞에 세워 뒀습니다."
    assert "wjdghldnjs9" in messages[0], f"어느 계정인지 없습니다: {messages[0]}"


async def test_login_takes_no_password_argument():
    """비밀번호를 받는 통로가 남아 있으면 언젠가 다시 자동 입력을 하게 된다."""
    import inspect

    for func in (BrowserSession._login, BrowserSession.open):
        params = inspect.signature(func).parameters
        assert not any("password" in name for name in params), (
            f"{func.__name__}이 아직 비밀번호를 받습니다: {list(params)}"
        )
