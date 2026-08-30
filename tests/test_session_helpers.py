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
