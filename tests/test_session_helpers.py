import json
import sys

import pytest

from engine.paths import AppPaths
from engine.session import (
    BadCredentials,
    BrowserSession,
    CaptchaRequired,
    TwoFactorRequired,
    classify_login_page,
    decrypt_bytes,
    encrypt_bytes,
    load_storage_state,
)


def test_successful_landing_classifies_as_none():
    assert classify_login_page("https://www.naver.com/", "") is None


def test_captcha_page_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login", "자동입력 방지 문자를 입력해 주세요"
    ) is CaptchaRequired


def test_two_factor_page_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login?mode=number", "2단계 인증"
    ) is TwoFactorRequired


def test_bad_credentials_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login",
        "아이디 또는 비밀번호를 잘못 입력했습니다",
    ) is BadCredentials


def test_still_on_login_page_without_a_known_message_is_generic_failure():
    result = classify_login_page("https://nid.naver.com/nidlogin.login", "무언가 다른 화면")
    assert result is not None
    assert issubclass(result, Exception)


def test_encrypt_round_trips():
    payload = b'{"cookies": [{"name": "NID_AUT"}]}'
    assert decrypt_bytes(encrypt_bytes(payload)) == payload


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI는 Windows 전용 — 비-Windows는 파일 권한으로 보호")
def test_encrypted_blob_does_not_contain_the_plaintext():
    payload = b"NID_AUT_secret_value"
    assert payload not in encrypt_bytes(payload)


# ---- MINOR: close()는 반복 호출에 안전해야 한다 ----
# 실제 브라우저 없이도 확인할 수 있는 부분만 — 세 핸들이 전부 None인 채로
# 시작해 close()를 두 번 불러도 예외 없이 끝나야 한다 (없는 핸들에 대한
# .close()/.stop() 호출을 건너뛰는 기존 None 방어와, 끝에서 핸들을 다시
# None으로 되돌리는 부분을 함께 검증한다).


async def test_close_before_open_is_a_no_op():
    session = BrowserSession(AppPaths.for_app())
    await session.close()
    assert session._browser is None
    assert session._context is None
    assert session._pw is None
    assert session.page is None


async def test_close_is_idempotent_when_called_twice():
    session = BrowserSession(AppPaths.for_app())
    await session.close()
    await session.close()      # 두 번째 호출도 예외 없이 끝나야 한다
    assert session._browser is None
    assert session._context is None
    assert session._pw is None
    assert session.page is None


# 2026-08-31 실제 화면에서 관측한 문구들. 로그인 폼에는 "일회용 번호 로그인"
# 링크가 늘 있으므로, 그 문구를 2차 인증 힌트로 쓰면 어떤 실패든 2차 인증으로
# 오분류된다 — 운영자가 캡차를 2차 인증으로 잘못 안내받는다.
LOGIN_FORM_TEXT = (
    "본문 바로가기 네이버 아이디 또는 전화번호 비밀번호 로그인 상태 유지 IP 보안 ON "
    "로그인 QR 코드 로그인 일회용 번호 로그인 아이디 찾기 비밀번호 찾기 회원가입"
)
EXTRA_VERIFY_TEXT = (
    "본문 바로가기 네이버 보안을 위해 추가 확인을 해주세요 "
    "가게 위치는 강릉시 양구군 [?] 172 입니다. (빈 칸을 채워주세요) 확인"
)


def test_plain_login_form_is_not_mistaken_for_two_factor():
    result = classify_login_page(
        "https://nid.naver.com/nidlogin.login", LOGIN_FORM_TEXT
    )
    assert result is not TwoFactorRequired, (
        "로그인 폼의 '일회용 번호 로그인' 링크를 2차 인증으로 오분류합니다."
    )


def test_additional_verification_page_is_recognized_as_captcha():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login", EXTRA_VERIFY_TEXT
    ) is CaptchaRequired


def test_saved_state_loads_back_as_a_mapping(tmp_path):
    """Playwright의 storage_state는 경로 또는 dict만 받는다.

    JSON 문자열을 주면 그것을 파일 경로로 취급해 FileNotFoundError를 내고,
    예외 메시지에 세션 쿠키 전체(NID_AUT 포함)가 실린다 — 2026-08-31 관측.
    """
    paths = AppPaths.for_app(tmp_path)
    paths.ensure()
    state_file = paths.session_file("acct")
    state_file.write_bytes(
        encrypt_bytes(json.dumps({"cookies": [{"name": "NID_AUT"}]}).encode("utf-8"))
    )

    loaded = load_storage_state(state_file)

    assert isinstance(loaded, dict), f"dict가 아니라 {type(loaded).__name__}입니다."
    assert loaded["cookies"][0]["name"] == "NID_AUT"


def test_missing_state_file_loads_as_none(tmp_path):
    assert load_storage_state(AppPaths.for_app(tmp_path).session_file("nobody")) is None


def test_corrupt_state_file_is_ignored(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    paths.ensure()
    state_file = paths.session_file("acct")
    state_file.write_bytes(b"not an encrypted json")

    assert load_storage_state(state_file) is None


# ---- 자동 입력이 막히면 창을 닫지 말고 운영자에게 넘긴다 ----
# 2026-08-31 관측: 자동 입력 로그인은 "보안을 위해 추가 확인" 화면을 부른다.
# 예전에는 여기서 CaptchaRequired를 던졌고, open()의 except가 close()로 창을
# 닫아버려 운영자는 눈앞의 문제를 풀 기회조차 없었다 — 그래서 tools/login.py를
# 따로 써야 했다. 이제는 창을 열어둔 채 사람이 끝내기를 기다린다. 문제를
# 프로그램이 푸는 것이 아니라 사람이 푼다.


class _FakeContext:
    """cookies()를 부를 때마다 준비된 답을 차례로 돌려준다."""

    def __init__(self, *answers: list[dict]) -> None:
        self._answers = list(answers)
        self.calls = 0

    async def cookies(self) -> list[dict]:
        self.calls += 1
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


class _FakeChallengePage:
    """로그인 호스트에 머무는 화면. 폼을 건드리는 호출은 존재하지 않는다."""

    def __init__(self, text: str = EXTRA_VERIFY_TEXT) -> None:
        self.url = "https://nid.naver.com/nidlogin.login?mode=form"
        self._text = text
        self.visited: list[str] = []

    def is_closed(self) -> bool:
        return False

    async def goto(self, url: str, **_kw) -> None:
        self.visited.append(url)
        self.url = url

    async def inner_text(self, _selector: str) -> str:
        return self._text


async def test_challenge_hands_the_window_to_the_operator(monkeypatch):
    """막힌 화면이 떠도 예외를 던지지 않고, 사람이 끝내면 그대로 진행한다."""
    session = BrowserSession(AppPaths.for_app())
    session.page = _FakeChallengePage()
    session._context = _FakeContext([{"name": "NID_AUT"}])   # 사람이 이미 끝냈다

    async def logged_in(_self) -> bool:
        return True

    monkeypatch.setattr(BrowserSession, "_is_logged_in", logged_in)

    seen: list[str] = []
    await session._login("someid", on_challenge=seen.append)

    assert seen, "운영자에게 알리지 않고 조용히 기다렸습니다."
    assert "추가 확인" in seen[0], f"무엇을 해야 하는지 알려주지 않았습니다: {seen[0]!r}"


async def test_manual_handoff_timeout_raises_the_original_failure(monkeypatch):
    """사람이 시간 안에 끝내지 못하면 화면에서 읽어낸 실패 종류를 그대로 던진다."""
    session = BrowserSession(AppPaths.for_app())
    session.page = _FakeChallengePage()
    session._context = _FakeContext([])      # 끝내 로그인되지 않는다

    async def logged_in(_self) -> bool:
        return False

    monkeypatch.setattr(BrowserSession, "_is_logged_in", logged_in)

    with pytest.raises(CaptchaRequired):
        await session._login("someid", manual_timeout_s=0)


async def test_plain_login_form_still_tells_the_operator_what_to_do(monkeypatch):
    """자동 입력을 하지 않으므로 보통은 평범한 로그인 폼이다 — 그래도 안내는 나간다."""
    session = BrowserSession(AppPaths.for_app())
    session.page = _FakeChallengePage(LOGIN_FORM_TEXT)
    session._context = _FakeContext([{"name": "NID_AUT"}])

    async def logged_in(_self) -> bool:
        return True

    monkeypatch.setattr(BrowserSession, "_is_logged_in", logged_in)

    seen: list[str] = []
    await session._login("someid", on_challenge=seen.append)

    assert seen and "로그인" in seen[0], f"안내가 비었습니다: {seen!r}"


# ---- 로그인 판정은 서버에 묻는다 (2026-09-10) ----
# 저장된 세션을 매번 컨텍스트에 다시 실어 주므로 NID_AUT는 네이버가 세션을
# 만료시킨 뒤에도 로컬에 그대로 남는다. 쿠키 '존재'만 보는 검사는 구조적으로
# 만료를 탐지할 수 없다. 실제로 그 상태로 실행이 끝까지 돌았고(공감 21건
# 전부 401), 더 나쁘게는 앱도 tools/login.py도 "이미 로그인되어 있습니다"라며
# 재로그인 기회를 주지 않아 복구 자체가 막혔다.


class _FakeCookieContext:
    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.cleared = False

    async def cookies(self) -> list[dict]:
        return [{"name": n} for n in self._names]

    async def clear_cookies(self, *, name: str | None = None, **_kw) -> None:
        self.cleared = True
        self._names = [n for n in self._names if n != name] if name else []


class _FakeNavPage:
    """로그인 필수 페이지를 흉내 낸다.

    `bounces=True`는 로그아웃 상태 — 네이버가 서버측 302로 nid 로그인 폼에
    데려다 놓는다. `bounces=False`는 세션이 살아 있어 그 페이지에 그대로
    도착한 상태다.
    """

    def __init__(self, *, bounces: bool = True) -> None:
        self.url = "about:blank"
        self._bounces = bounces
        self.visited: list[str] = []

    async def goto(self, url: str, **_kw) -> None:
        self.visited.append(url)
        self.url = (
            f"https://nid.naver.com/nidlogin.login?mode=form&url={url}"
            if self._bounces
            else url
        )


async def test_stale_cookie_alone_is_not_accepted_as_logged_in():
    """만료된 세션은 쿠키가 남아 있어도 로그아웃으로 판정돼야 한다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeCookieContext(["NID_AUT", "NID_SES"])
    session.page = _FakeNavPage(bounces=True)   # 로그인 폼으로 튕긴다

    assert await session._is_logged_in() is False


async def test_a_live_session_reaches_the_login_only_page():
    """세션이 살아 있으면 로그인 필수 페이지에 그대로 도착한다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeCookieContext(["NID_AUT", "NID_SES"])
    session.page = _FakeNavPage(bounces=False)

    assert await session._is_logged_in() is True


async def test_no_cookie_at_all_skips_the_network_entirely():
    """쿠키가 아예 없으면 볼 것도 없이 로그아웃이다 — 네트워크를 타지 않는다."""
    session = BrowserSession(AppPaths.for_app())
    session._context = _FakeCookieContext([])
    session.page = _FakeNavPage()

    assert await session._is_logged_in() is False
    assert session.page.visited == []


async def test_stale_cookies_are_discarded_before_a_fresh_login():
    """낡은 NID_AUT를 남겨 두면 wait_for_manual_login이 그것을 보고 곧바로
    '로그인 완료'로 판정한다 — 사람이 아무것도 하지 않았는데도."""
    session = BrowserSession(AppPaths.for_app())
    context = _FakeCookieContext(["NID_AUT", "NID_SES"])
    session._context = context

    await session._discard_stale_session()

    assert context.cleared is True
    assert await session._has_auth_cookie() is False
