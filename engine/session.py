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
# 로그인 여부를 묻는 자리. **로그인 페이지는 답이 될 수 없다.**
#
# 2026-09-11까지 이 검사는 LOGIN_URL을 열고 "아직 nid.naver.com인가"로 판정했다.
# 그 질문에는 답이 없다 — 로그인 폼이 보인다는 사실은 로그인 상태에서도
# 로그아웃 상태에서도 일어날 수 있다. 실측(2026-09-11, 로그아웃 컨텍스트)에서
# 두 주소는 로그아웃일 때 구분이 되지 않았다:
#     nidlogin.login?mode=form   → nid에 머묾   (원래 그 자리가 로그인 폼이다)
#     blog.naver.com/MyBlog.naver → nid로 302   (로그인해야만 볼 수 있으므로)
# 차이는 로그인 상태에서만 드러난다. 앞의 것은 "네이버가 우리를 돌려보내 준다"는
# 보장되지 않은 동작에 기대고, 뒤의 것은 "로그인 필수 페이지는 로그아웃일 때
# 튕긴다"는 정의에 기댄다. 그래서 뒤의 것을 쓴다.
#
# blog.naver.com을 고른 이유: 공감 API가 사는 바로 그 도메인이라 "공감이 될
# 세션인가"를 가장 가깝게 묻고, 평범한 블로그 트래픽이라 가장 방어가 심한
# nid를 실행마다 두드리지 않는다 (결정 3).
LOGIN_PROBE_URL = "https://blog.naver.com/MyBlog.naver"
LOGIN_PROBE_TIMEOUT_MS = 20_000
LOGIN_TRANSITION_TIMEOUT_MS = 15_000
# 수동 로그인은 사람이 추가 확인·보호조치를 푸는 시간이다.
#
# 300초였고, 그것이 운영자가 본인확인을 하는 도중에 창을 닫아 버렸다
# (2026-09-11: "보호조치로 해제하니깐 꺼졌어"). 휴대폰 본인확인이 들어가는
# 보호조치 해제는 5분 안에 끝나지 않는다. 타이머가 끝나면 bootstrap_manual의
# finally가 close()를 부르고, 거기까지 한 일이 전부 날아간다.
#
# 그래서 대기의 끝은 타이머가 아니라 사람이다 — 로그인이 끝나거나, 사람이 창을
# 닫거나(포기). 아래 값은 잊고 자리를 뜬 경우를 위한 뒷받침일 뿐이라 넉넉하다.
MANUAL_LOGIN_TIMEOUT_S = 1800
MANUAL_LOGIN_POLL_S = 1.0
# 말없이 멈춰 있는 창 앞에서는 기다리는 중인지 죽은 것인지 알 수 없다.
MANUAL_LOGIN_NOTICE_S = 60
# 폼 셀렉터는 우리가 쓰지 않는다 (자동 입력 폐지, 2026-09-11) — 사람이 창에서
# 직접 누른다. 그래도 상수와 계약 테스트는 남긴다: 이 셀렉터가 사라지면
# 네이버가 로그인 화면을 갈아엎었다는 뜻이고, 그건 classify_login_page의 문구
# 힌트도 같이 낡았다는 신호다 (레거시 결함 2가 재발하는 자리).
# 만료된 세션에서 버릴 것은 정확히 이 두 개다.
#
# 2026-09-11: `_discard_stale_session()`이 `clear_cookies()`를 인자 없이 불러
# 쿠키를 통째로 비웠다. 저장된 세션에는 인증 쿠키만 있는 게 아니다 — 실측하면
# NNB · NAC · NACT · BUC · nid_inf 같은 **기기/방문자 식별 쿠키**가 함께 들어
# 있고(계정 하나 기준 14개 중 대부분), 그것까지 지우면 다음에 열리는 로그인
# 창에는 네이버가 이 브라우저를 알아볼 단서가 하나도 없다. 즉 매번 "처음 보는
# 기기에서의 로그인"이 되고, 그것이 추가 확인과 보호조치를 부르는 조건이다.
# 자동 입력을 없앤 뒤에도 보호조치가 계속 뜬 이유가 이것이었다.
#
# 만료된 세션을 버리는 일과 기기 신원을 버리는 일은 전혀 다른 일이다.
AUTH_COOKIES = ("NID_AUT", "NID_SES")
LOGIN_ID = "#id"
LOGIN_PW = "#pw"
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
        *,
        headless: bool = False,
        on_challenge: Callable[[str], None] | None = None,
    ) -> "BrowserSession":
        """세션이 살아 있으면 그대로 쓰고, 아니면 로그인 창을 열어 사람에게 넘긴다.

        비밀번호를 받지 않는다 — _login()이 자격증명을 입력하지 않기 때문이다.
        """
        state_file = self._paths.session_file(account)

        try:
            await self._start(state_file, headless=headless)

            if not await self._is_logged_in():
                await self._discard_stale_session()
                await self._login(account, on_challenge=on_challenge)
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
        notify: Callable[[str], None] | None = None,
    ) -> bool:
        """사람이 창에서 직접 로그인을 끝낼 때까지 기다린다.

        페이지를 이동시키지 않는다 — 운영자가 입력하고 있는 화면을 가로채면
        로그인 자체가 불가능해진다. 쿠키만 들여다본다.

        끝나는 조건은 셋이다:
          · NID_AUT가 생겼다            → True  (로그인 완료)
          · 사람이 창을 닫았다           → False (포기. 더 기다릴 이유가 없다)
          · timeout_s가 지났다          → False (잊고 자리를 뜬 경우의 뒷받침)

        창이 닫혔는지를 보는 것이 핵심이다. 그게 없으면 타이머 하나가 대기의
        유일한 끝이 되고, 그 타이머가 짧으면 본인확인 중인 사람의 창을 닫는다
        (2026-09-11). 길게 잡되 사람이 언제든 끝낼 수 있게 한다.
        """
        deadline = time.monotonic() + timeout_s
        next_notice = time.monotonic() + MANUAL_LOGIN_NOTICE_S
        say = notify or (lambda _message: None)
        while True:
            if self._window_is_gone():
                return False
            try:
                cookies = await self._context.cookies()
            except Exception:
                # 브라우저가 통째로 닫히면 컨텍스트 조회가 실패한다 — 포기와 같다.
                return False
            if any(c["name"] == "NID_AUT" for c in cookies):
                return True

            now = time.monotonic()
            if now >= deadline:
                return False
            if now >= next_notice:
                say(f"로그인 완료를 기다리는 중입니다 — 남은 시간 "
                    f"{max(1, int((deadline - now) / 60))}분. 창을 닫으면 중단됩니다.")
                next_notice = now + MANUAL_LOGIN_NOTICE_S
            await asyncio.sleep(poll_s)

    def _window_is_gone(self) -> bool:
        """운영자가 로그인 창을 닫았는가."""
        page = self.page
        return page is not None and page.is_closed()

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

            if not await self.wait_for_manual_login(timeout_s=timeout_s, notify=say):
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
        """만료된 **인증** 쿠키만 버린다. 기기 신원은 남긴다.

        낡은 NID_AUT를 남겨 두면 wait_for_manual_login()이 그것을 보고 사람이
        아무것도 하지 않았는데 "로그인 완료"로 판정한다 — 그래서 지워야 한다.

        그런데 통째로 비우면 NNB 같은 기기 식별 쿠키까지 날아가고, 네이버는
        그 다음 로그인을 처음 보는 기기에서의 로그인으로 본다. 보호조치가
        거기서 나온다 (2026-09-11). 지울 것과 남길 것을 구분한다.
        """
        for name in AUTH_COOKIES:
            await self._context.clear_cookies(name=name)

    async def _is_logged_in(self) -> bool:
        """실제로 로그인 상태인지 **네이버에게** 묻는다.

        쿠키 존재는 답이 될 수 없다. 저장된 세션을 매번 컨텍스트에 다시 실어
        주므로 NID_AUT는 네이버가 세션을 만료시킨 뒤에도 로컬에 남는다 — 즉
        "쿠키가 있는가"는 구조적으로 만료를 탐지할 수 없다. 2026-09-10에
        실제로 그 상태로 실행이 끝까지 돌았다 (공감 21건 전부 401 거부).

        그렇다고 로그인 페이지에 물어서도 안 된다. 2026-09-11까지 그렇게 했고,
        "로그인 폼이 보이면 로그아웃"이라는 판정이 살아 있는 세션까지 로그아웃으로
        몰았다. 그 대가가 두 겹이었다 — 멀쩡한 세션을 버리고 매 실행 자동
        비밀번호 입력으로 가서 계정 보호 조치를 불렀고, 사람이 손으로 로그인을
        끝낸 직후에도 같은 판정이 False라서 SessionExpired로 터졌다.

        판정은 두 단계다:
          1. 쿠키가 아예 없으면 볼 것도 없이 로그아웃이다. 네트워크를 타지 않는다.
          2. 있으면 **로그인해야만 볼 수 있는 페이지**(LOGIN_PROBE_URL)를 연다.
             세션이 죽었으면 네이버가 서버측 302로 nid 로그인 폼에 데려다
             놓는다. 로그인 폼에 도착했다 = 로그아웃. 명확한 한 방향 신호다.

        판정 불가(타임아웃 등)는 로그아웃으로 친다. 반대로 틀리면 실행 전체가
        로그아웃 상태로 돌며 공감이 전부 401로 거부된다 — 2026-09-10 사고가
        정확히 그것이었다. 틀린 쪽의 대가가 훨씬 싸다: 필요 없는 로그인 창이
        한 번 열릴 뿐이다.
        """
        if not await self._has_auth_cookie():
            return False
        try:
            await self.page.goto(
                LOGIN_PROBE_URL,
                wait_until="domcontentloaded",
                timeout=LOGIN_PROBE_TIMEOUT_MS,
            )
        except PlaywrightTimeout:
            return False
        return LOGIN_HOST not in self.page.url

    async def _login(
        self,
        account: str,
        *,
        on_challenge: Callable[[str], None] | None = None,
        manual_timeout_s: float = MANUAL_LOGIN_TIMEOUT_S,
    ) -> None:
        """로그인 창을 열어 운영자에게 넘긴다. 자격증명을 대신 입력하지 않는다.

        2026-09-11 이전에는 아이디/비밀번호를 자동 입력했다. 그 동작이 바로
        네이버의 "보안을 위해 추가 확인" 화면을 부르는 것이고(2026-08-31 실측),
        그 화면은 어차피 사람이 풀어야 한다. 즉 자동 입력이 계정에 남기는 것은
        "실패한 자동 로그인 시도" 기록뿐이고, 그것이 반복되면 보호 조치로
        이어진다 — 2026-09-11 실행 로그가 3분 간격 연속 자동 로그인을 보여
        준다. 계정 안전은 1급 요구사항이므로(설계 원칙) 시도 자체를 하지
        않는다. 운영자 결정, 2026-09-11.

        세션이 살아 있으면 이 함수는 호출되지도 않는다 (결정 3). 여기까지
        왔다는 것은 세션이 정말 없거나 만료됐다는 뜻이고, 그때 필요한 것은
        사람이 한 번 로그인해 주는 일이다.

        account는 안내에만 쓴다 — 창에 입력하지 않는다.
        """
        say = on_challenge or (lambda _message: None)
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # 왜 로그인 화면이 필요한지는 그대로 분류해 알려준다. 캡차인데 2차
        # 인증이라고 안내하면 운영자가 엉뚱한 곳을 본다. 자동 입력을 하지
        # 않으므로 보통은 평범한 로그인 폼이고, 그때는 일반 안내가 나간다.
        body_text = await self.page.inner_text("body")
        failure = classify_login_page(self.page.url, body_text) or LoginError
        say(f"[{account}] {challenge_message(failure)}")

        if not await self.wait_for_manual_login(
            timeout_s=manual_timeout_s, notify=say
        ):
            raise failure("로그인이 확인되지 않았습니다 (창이 닫혔거나 시간이 지났습니다).")

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
