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


class _FakeKeyboard:
    async def insert_text(self, text: str) -> None:
        pass


class _FakeLocator:
    def __init__(self, page: "_FakeLoginPage") -> None:
        self._page = page

    def locator(self, _selector: str) -> "_FakeLocator":
        return self

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def click(self) -> None:
        self._page.clicked = True


class _FakeLoginPage:
    """클릭 직후에는 아직 로그인 폼에 머물러 있고, 기다려야 전이가 끝난다.

    실제 관측(2026-08-31): 클릭 후 판정 시점의 URL이 여전히 로그인 폼이었다.
    wait_for_load_state("domcontentloaded")는 현재 문서가 이미 로드돼 있으면
    즉시 반환하므로 전이를 기다려 주지 않는다.
    """

    def __init__(self) -> None:
        self.url = "https://nid.naver.com/nidlogin.login?mode=form"
        self.clicked = False
        self.waited = False
        self.keyboard = _FakeKeyboard()

    async def goto(self, url: str, **_kw) -> None:
        self.url = url

    async def click(self, _selector: str) -> None:
        pass

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(self)

    async def wait_for_url(self, _predicate, **_kw) -> None:
        self.waited = True
        self.url = "https://www.naver.com/"

    async def wait_for_load_state(self, *_a, **_kw) -> None:
        pass

    async def inner_text(self, _selector: str) -> str:
        return LOGIN_FORM_TEXT if "nid.naver.com" in self.url else "네이버 메인"


async def test_login_waits_for_the_post_click_transition_before_judging(monkeypatch):
    session = BrowserSession(AppPaths.for_app())
    session.page = _FakeLoginPage()

    async def logged_in(_self) -> bool:
        return True

    monkeypatch.setattr(BrowserSession, "_is_logged_in", logged_in)

    await session._login("someid", "somepw")   # 예외가 나면 안 된다

    assert session.page.waited, "클릭 후 전이를 기다리지 않고 화면을 판정했습니다."


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
