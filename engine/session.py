"""브라우저 기동 · 로그인 · 계정별 세션 저장.

레거시는 로그인 예외를 로그만 남기고 진행해서, driver가 살아 있으면 로그아웃
상태로 전 과정을 돌며 모든 공감이 실패했다 (결함 3). 여기서는 로그인이
확인되지 않으면 예외를 올리고 워커가 시작조차 하지 않는다.

세션 파일은 네이버 세션 쿠키다 — 훔치면 비밀번호 없이 로그인된다. 자격증명을
키링에 넣고 세션을 평문으로 두면 보호가 반감되므로 암호화해 저장한다.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

from engine.paths import AppPaths

LOGIN_URL = (
    "https://nid.naver.com/nidlogin.login?mode=form"
    "&url=https%3A%2F%2Fwww.naver.com&locale=ko_KR&svctype=1"
)
LOGIN_HOST = "nid.naver.com"
LOGIN_TRANSITION_TIMEOUT_MS = 15_000
# 수동 로그인은 사람이 추가 확인 문제를 푸는 시간이다. 넉넉해야 한다.
MANUAL_LOGIN_TIMEOUT_S = 300
MANUAL_LOGIN_POLL_S = 1.0
LOGIN_ID = "#id"
LOGIN_PW = "#pw"
# 로그인 버튼은 반응형 레이아웃 때문에 DOM에 두 벌(column/row)로 들어 있고 그중
# 한쪽만 보인다. 그래서 항상 "보이는 것"으로 좁혀서 클릭한다. 예전 `.btn_login`은
# 2026-08-31 확인 시 페이지에서 사라져 있었다 — 실행 시점의 30초 타임아웃으로만
# 드러났으므로 tests/test_login_page_contract.py가 이 세 셀렉터를 감시한다.
LOGIN_BUTTON = "#loginBtn_column, #loginBtn_row"


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


# 2026-08-31 관측: 자동 입력으로 로그인하면 네이버가 "보안을 위해 추가 확인을
# 해주세요" 화면(이미지 문제)을 띄운다. 문구에 "자동입력 방지"도 "보안 문자"도
# 없으므로 별도 힌트가 필요하다.
_CAPTCHA_HINTS = ("자동입력 방지", "captcha", "보안 문자", "추가 확인")
# "일회용 번호"는 넣으면 안 된다 — 평범한 로그인 폼에도 "일회용 번호 로그인"
# 링크가 늘 있어서 모든 실패가 2차 인증으로 오분류된다.
_TWO_FACTOR_HINTS = ("2단계 인증", "인증번호를 입력")
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


_CHALLENGE_MESSAGE = {
    CaptchaRequired: "브라우저 창에서 추가 확인(이미지) 문제를 풀어 주세요.",
    TwoFactorRequired: "브라우저 창에서 2차 인증을 완료해 주세요.",
    BadCredentials: "아이디 또는 비밀번호가 맞지 않습니다. 창에서 직접 로그인해 주세요.",
}


def challenge_message(failure: type[LoginError]) -> str:
    """막힌 이유에 맞는 안내 문구. 운영자는 이걸 보고 창에서 무엇을 할지 안다."""
    return _CHALLENGE_MESSAGE.get(
        failure, "브라우저 창에서 로그인을 완료해 주세요."
    )


def load_storage_state(state_file: Path) -> dict | None:
    """저장된 세션을 Playwright가 받는 형태(dict)로 읽는다.

    new_context(storage_state=...)는 파일 경로 아니면 dict만 받는다. JSON
    문자열을 그대로 넘기면 그것을 경로로 여겨 FileNotFoundError를 내는데, 그
    예외 메시지에 세션 쿠키 전체(NID_AUT 포함)가 실린다 — 로그·화면으로
    자격증명이 새는 경로다 (2026-08-31 관측).

    읽을 수 없는 세션은 None으로 돌려 새 로그인으로 넘긴다.
    """
    if not state_file.exists():
        return None
    try:
        return json.loads(decrypt_bytes(state_file.read_bytes()).decode("utf-8"))
    except Exception:
        return None    # 손상된 세션은 무시하고 새로 로그인한다


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
        on_challenge: Callable[[str], None] | None = None,
    ) -> "BrowserSession":
        state_file = self._paths.session_file(account)

        try:
            await self._start(state_file, headless=headless)

            if not await self._is_logged_in():
                await self._discard_stale_session()
                await self._login(
                    account, password_supplier(), on_challenge=on_challenge
                )
                await self._save_state(state_file)
        except Exception:
            # 캡차·2차인증·자격증명 오류는 정상 운영 중에도 자주 일어난다
            # (세션 재사용을 두는 이유 자체가 그것). 브라우저 기동 자체가 실패할
            # 수도 있다 (브라우저 바이너리 누락, 리소스 고갈 등). 어느 단계에서
            # 실패하든 열어둔 핸들을 남긴 채로 예외를 올려서는 안 된다 — 매번
            # 프로세스가 남는다. close()는 각 핸들을 None 여부로 방어하므로
            # 생성 도중 어느 지점에서 멈췄어도 안전하다.
            await self.close()
            raise
        return self

    async def _start(self, state_file: Path, *, headless: bool = False) -> None:
        """브라우저·컨텍스트·탭을 연다. 저장된 세션이 있으면 실어 준다."""
        storage_state = load_storage_state(state_file)

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        self._context = await self._browser.new_context(storage_state=storage_state)
        self.page = await self._context.new_page()

    async def wait_for_manual_login(
        self,
        *,
        timeout_s: float = MANUAL_LOGIN_TIMEOUT_S,
        poll_s: float = MANUAL_LOGIN_POLL_S,
    ) -> bool:
        """사람이 창에서 직접 로그인을 끝낼 때까지 기다린다.

        _is_logged_in()과 달리 페이지를 이동시키지 않는다 — 운영자가 입력하고
        있는 화면을 가로채면 로그인 자체가 불가능해진다. 쿠키만 들여다본다.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            cookies = await self._context.cookies()
            if any(c["name"] == "NID_AUT" for c in cookies):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll_s)

    async def bootstrap_manual(
        self,
        account: str,
        *,
        timeout_s: float = MANUAL_LOGIN_TIMEOUT_S,
        notify: Callable[[str], None] | None = None,
    ) -> bool:
        """최초 1회 수동 로그인 — 자격증명을 입력하지 않고 세션만 저장한다.

        네이버는 자동 입력 로그인에 "보안을 위해 추가 확인" 화면을 띄운다
        (2026-08-31 관측). 그 화면은 사람이 풀어야 한다. 여기서 저장해 둔 세션
        덕분에 이후 실행은 로그인 페이지를 거의 거치지 않는다 (결정 3).
        """
        state_file = self._paths.session_file(account)
        say = notify or (lambda _message: None)
        try:
            await self._start(state_file, headless=False)

            if await self._is_logged_in():
                await self._save_state(state_file)
                say("이미 로그인되어 있습니다. 세션을 갱신했습니다.")
                return True

            # 낡은 쿠키를 남겨 두면 wait_for_manual_login()이 그것을 보고
            # 사람이 손대기도 전에 "완료"로 판정한다.
            await self._discard_stale_session()
            await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
            say("열린 창에서 직접 로그인해 주세요. 완료를 기다립니다.")

            if not await self.wait_for_manual_login(timeout_s=timeout_s):
                say("시간 안에 로그인이 끝나지 않았습니다.")
                return False
            if not await self._is_logged_in():
                say("로그인 쿠키는 생겼지만 세션이 확인되지 않았습니다.")
                return False

            await self._save_state(state_file)
            say("로그인 확인됨 — 세션을 저장했습니다.")
            return True
        finally:
            await self.close()

    async def _has_auth_cookie(self) -> bool:
        """로그인 쿠키가 컨텍스트에 들어 있는가. **로그인 여부가 아니다.**"""
        cookies = await self._context.cookies()
        return any(c["name"] == "NID_AUT" for c in cookies)

    async def _discard_stale_session(self) -> None:
        """로그아웃으로 판정된 뒤 남아 있는 쿠키를 버린다.

        저장된 세션은 우리가 컨텍스트에 실어 준 것이라, 만료된 뒤에도 그대로
        남는다. 그걸 남겨 두면 wait_for_manual_login()이 그 NID_AUT를 보고
        사람이 아무것도 하지 않았는데 "로그인 완료"로 판정한다. 새 로그인을
        시작하기 전에 깨끗이 지운다.
        """
        await self._context.clear_cookies()

    async def _is_logged_in(self) -> bool:
        """실제로 로그인 상태인지 **네이버에게** 묻는다.

        쿠키 존재는 답이 될 수 없다. 저장된 세션을 매번 컨텍스트에 다시 실어
        주므로 NID_AUT는 네이버가 세션을 만료시킨 뒤에도 로컬에 남는다 — 즉
        "쿠키가 있는가"는 구조적으로 만료를 탐지할 수 없다. 2026-09-10에
        실제로 그 상태로 실행이 끝까지 돌았고(공감 21건 전부 401 거부), 더
        나쁘게는 앱도 tools/login.py도 "이미 로그인되어 있습니다"라고 답해
        재로그인 자체가 막혔다. 레거시 결함 3이 다른 얼굴로 돌아온 것이다.

        판정은 두 단계다:
          1. 쿠키가 아예 없으면 볼 것도 없이 로그아웃이다. 로그인 페이지는
             가장 방어가 심한 화면이므로(결정 3) 갈 이유가 없으면 가지 않는다.
          2. 있으면 로그인 페이지를 열어 본다. 세션이 살아 있으면 네이버가
             우리를 url 파라미터(www.naver.com)로 돌려보낸다. 이것은
             _login()이 로그인 성공을 판정할 때 쓰는 것과 **같은 신호**다 —
             새 신호를 지어내지 않는다. 폼을 건드리지도, 아무것도 입력하지도
             않는다.
        """
        if not await self._has_auth_cookie():
            return False
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        return LOGIN_HOST not in self.page.url

    async def _login(
        self,
        account: str,
        password: str,
        *,
        on_challenge: Callable[[str], None] | None = None,
        manual_timeout_s: float = MANUAL_LOGIN_TIMEOUT_S,
    ) -> None:
        """자동 입력으로 로그인하고, 막히면 그 창을 운영자에게 넘긴다.

        예전에는 막힌 화면을 분류해 곧장 예외로 올렸고, open()의 except가
        close()로 창을 닫았다 — 운영자는 눈앞의 추가 확인 문제를 풀 기회조차
        없었다. 그래서 tools/login.py가 따로 필요했다. 이제는 창을 열어둔 채
        기다린다. 문제를 푸는 것은 언제나 사람이다.
        """
        say = on_challenge or (lambda _message: None)
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        if password:
            # fill()은 탐지되기 쉽다. insert_text는 키 이벤트 없이 값을 넣는다
            # (레거시의 클립보드 붙여넣기와 같은 효과).
            await self.page.click(LOGIN_ID)
            await self.page.keyboard.insert_text(account)
            await self.page.click(LOGIN_PW)
            await self.page.keyboard.insert_text(password)

            await self.page.locator(LOGIN_BUTTON).locator("visible=true").first.click()

            # click()은 그 클릭이 일으킨 이동을 기다려 주지 않고,
            # wait_for_load_state("domcontentloaded")는 현재 문서가 이미 로드돼
            # 있으면 즉시 반환한다 — 그래서 전이가 끝나기 전의 로그인 폼을 읽고
            # 실패로 오판했다(2026-08-31 관측). 로그인 호스트를 벗어날 때까지
            # 기다리고, 끝내 벗어나지 못하면 그 화면을 분류한다.
            try:
                await self.page.wait_for_url(
                    lambda url: LOGIN_HOST not in url,
                    timeout=LOGIN_TRANSITION_TIMEOUT_MS,
                )
            except PlaywrightTimeout:
                pass
        # 비밀번호가 없으면 폼을 건드리지 않는다. 빈 값을 밀어 넣어 봐야
        # 네이버에 실패한 로그인 시도만 남는다 — 창만 열어 주면 된다.

        body_text = await self.page.inner_text("body")
        failure = classify_login_page(self.page.url, body_text)
        if failure is not None:
            # 왜 막혔는지는 그대로 분류해 운영자에게 알려주고(캡차인데 2차
            # 인증이라고 안내하면 엉뚱한 곳을 보게 된다), 창은 열어 둔다.
            say(challenge_message(failure))
            if not await self.wait_for_manual_login(timeout_s=manual_timeout_s):
                raise failure(f"로그인이 확인되지 않았습니다. 현재 주소: {self.page.url}")

        if not await self._is_logged_in():
            raise SessionExpired("로그인 직후 세션이 확인되지 않았습니다.")

    async def _save_state(self, state_file: Path) -> None:
        state = await self._context.storage_state()
        _write_secret(state_file, json.dumps(state).encode("utf-8"))

    async def close(self) -> None:
        """호출을 반복해도 안전하다.

        open()이 LoginError로 예외를 올리기 전에 이미 스스로 close()를
        호출해 둔다(캡차 · 2차인증 · 자격증명 오류 등). 그런데
        desktop/app.py의 _run_engine은 LoginError를 잡은 뒤 다시 한 번
        close()를 부른다 — 그러면 이 핸들들이 None으로 리셋되지 않았을 때
        `_pw.stop()` 등이 두 번째로 호출된다. 검증되지 않은 채로 그 지점에서
        예외가 나면 원래의 BadCredentials가 가려지고 운영자에게 엉뚱한
        원인이 표시된다. 끝에서 각 핸들을 None으로 되돌려 반복 호출을
        아무 일도 하지 않는 것으로 만든다.
        """
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._context = None
        self._browser = None
        self._pw = None
        self.page = None
