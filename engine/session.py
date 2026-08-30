"""브라우저 기동 · 로그인 · 계정별 세션 저장.

레거시는 로그인 예외를 로그만 남기고 진행해서, driver가 살아 있으면 로그아웃
상태로 전 과정을 돌며 모든 공감이 실패했다 (결함 3). 여기서는 로그인이
확인되지 않으면 예외를 올리고 워커가 시작조차 하지 않는다.

세션 파일은 네이버 세션 쿠키다 — 훔치면 비밀번호 없이 로그인된다. 자격증명을
키링에 넣고 세션을 평문으로 두면 보호가 반감되므로 암호화해 저장한다.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from engine.paths import AppPaths

LOGIN_URL = (
    "https://nid.naver.com/nidlogin.login?mode=form"
    "&url=https%3A%2F%2Fwww.naver.com&locale=ko_KR&svctype=1"
)
LOGIN_HOST = "nid.naver.com"


class LoginError(Exception):
    """로그인이 확인되지 않았다."""


class CaptchaRequired(LoginError):
    pass


class TwoFactorRequired(LoginError):
    pass


class BadCredentials(LoginError):
    pass


class SessionExpired(LoginError):
    pass


_CAPTCHA_HINTS = ("자동입력 방지", "captcha", "보안 문자")
_TWO_FACTOR_HINTS = ("2단계 인증", "일회용 번호", "인증번호를 입력")
_BAD_CRED_HINTS = ("아이디 또는 비밀번호", "다시 확인해주세요", "로그인 정보가")


def classify_login_page(url: str, page_text: str) -> type[LoginError] | None:
    """로그인 시도 후의 화면을 보고 실패 유형을 가른다.

    레거시는 캡차·2차인증·비밀번호 오류를 뭉뚱그렸다. 대응이 각각 다르므로
    구분해야 한다. 문구는 네이버가 바꿀 수 있으므로, 어느 힌트에도 걸리지
    않아도 로그인 호스트에 남아 있으면 일반 LoginError로 처리한다.
    """
    if LOGIN_HOST not in url:
        return None

    haystack = page_text.lower()
    if any(h.lower() in haystack for h in _CAPTCHA_HINTS):
        return CaptchaRequired
    if any(h.lower() in haystack for h in _TWO_FACTOR_HINTS):
        return TwoFactorRequired
    if any(h.lower() in haystack for h in _BAD_CRED_HINTS):
        return BadCredentials
    return LoginError


def encrypt_bytes(data: bytes) -> bytes:
    """Windows는 DPAPI(로그인 사용자 계정에 묶임), 그 외는 그대로 둔다.

    비-Windows에서는 호출자가 파일 권한 0600으로 보호한다.
    """
    if sys.platform == "win32":
        import win32crypt

        return win32crypt.CryptProtectData(data, None, None, None, None, 0)
    return data


def decrypt_bytes(blob: bytes) -> bytes:
    if sys.platform == "win32":
        import win32crypt

        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    return blob


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(data))
    if sys.platform != "win32":
        path.chmod(0o600)


class BrowserSession:
    """브라우저 컨텍스트 하나 + 탭 하나. 공감은 단일 스트림이다 (결정 4)."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def open(
        self,
        account: str,
        password_supplier: Callable[[], str],
        *,
        headless: bool = False,
    ) -> "BrowserSession":
        state_file = self._paths.session_file(account)
        storage_state = None
        if state_file.exists():
            try:
                storage_state = decrypt_bytes(state_file.read_bytes()).decode("utf-8")
            except Exception:
                storage_state = None    # 손상된 세션은 무시하고 새로 로그인한다

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        self._context = await self._browser.new_context(storage_state=storage_state)
        self.page = await self._context.new_page()

        try:
            if not await self._is_logged_in():
                await self._login(account, password_supplier())
                await self._save_state(state_file)
        except Exception:
            # 캡차·2차인증·자격증명 오류는 정상 운영 중에도 자주 일어난다
            # (세션 재사용을 두는 이유 자체가 그것). 여기서 실패하면 브라우저를
            # 열어둔 채로 예외를 올려서는 안 된다 — 매번 프로세스가 남는다.
            await self.close()
            raise
        return self

    async def _is_logged_in(self) -> bool:
        """실제로 로그인 상태인지 확인한다. 쿠키 존재만으로는 부족하다."""
        await self.page.goto("https://blog.naver.com/", wait_until="domcontentloaded")
        if LOGIN_HOST in self.page.url:
            return False
        cookies = await self._context.cookies()
        return any(c["name"] == "NID_AUT" for c in cookies)

    async def _login(self, account: str, password: str) -> None:
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # fill()은 탐지되기 쉽다. insert_text는 키 이벤트 없이 값을 넣는다
        # (레거시의 클립보드 붙여넣기와 같은 효과).
        await self.page.click("#id")
        await self.page.keyboard.insert_text(account)
        await self.page.click("#pw")
        await self.page.keyboard.insert_text(password)

        await self.page.click(".btn_login")
        await self.page.wait_for_load_state("domcontentloaded")

        body_text = await self.page.inner_text("body")
        failure = classify_login_page(self.page.url, body_text)
        if failure is not None:
            raise failure(f"로그인이 확인되지 않았습니다. 현재 주소: {self.page.url}")

        if not await self._is_logged_in():
            raise SessionExpired("로그인 직후 세션이 확인되지 않았습니다.")

    async def _save_state(self, state_file: Path) -> None:
        import json

        state = await self._context.storage_state()
        _write_secret(state_file, json.dumps(state).encode("utf-8"))

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
