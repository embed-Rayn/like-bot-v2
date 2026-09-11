"""공감 클릭.

결과를 유형으로 반환한다. 레거시는 이 일곱 가지를 "공감 없음 or 이미 함" 한
줄로 뭉갰다. 유형을 나누는 이유는 표시가 아니라 대응이 각각 다르기 때문이다
(스펙 §7.1).

클릭 후 상태가 실제로 바뀌었는지 확인한다. 확인 없는 클릭은 거짓 성공을 만들고,
그러면 차단 감지가 무력해진다.
"""
from __future__ import annotations

import asyncio
import re
import time

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from engine.models import LikeOutcome, LikeResult
from engine.session import LOGIN_HOST, LOGIN_PROBE_URL, LOGIN_PROBE_TIMEOUT_MS

# 2026-08-31 실측. 클래스가 u_likeit_list_btn → u_likeit_button _face 로 바뀌었고,
# 글 하나에 같은 버튼이 둘 렌더링된다.
#   [0] 스크롤을 따라오는 플로팅 버튼. 늘 뷰포트 바로 아래(y = 뷰포트 높이 + 6)에
#       머물러서, 뷰포트를 키우든 부모를 스크롤하든 클릭이
#       "element is outside of the viewport"로 타임아웃된다.
#   [1] 본문 안 버튼. 조상이 #area_sympathy{logNo} 이고 스크롤해서 누를 수 있다.
# 그래서 글 번호로 범위를 좁혀 본문 안 버튼만 고른다 — 같은 페이지에 딸려 오는
# 다른 글의 버튼을 잘못 누를 위험도 함께 사라진다.
LIKE_BUTTON = "a.u_likeit_button._face"
LIKE_BUTTON_FALLBACK = f"div.area_sympathy.pcol2 {LIKE_BUTTON}"

# 공감 위젯은 **지연 초기화**된다 (실측 2026-09-10). 정적 마크업에는 늘
# class="... off" · aria-pressed="false" · 카운트 0인 껍데기가 들어 있고,
# 스크롤해서 화면에 들어온 뒤에야 스크립트가 서버의 진짜 상태를 채운다.
# 그 표시가 이 속성이다.
#   <div class="u_likeit_list_module ..." data-loaded="1">
# 초기화 전에 상태를 읽으면 이미 공감한 글도 "안 눌림"으로 보이고, 초기화
# 전에 클릭하면 onclick="return false"인 맨 <a>를 누르는 셈이라 아무 일도
# 일어나지 않는다.
LIKE_MODULE = ".u_likeit_list_module"
WIDGET_READY_TIMEOUT_MS = 8_000
WIDGET_POLL_INTERVAL_S = 0.25


def like_button_selector(log_no: str) -> str:
    return f"#area_sympathy{log_no} {LIKE_BUTTON}"


def like_module_selector(log_no: str) -> str:
    return f"#area_sympathy{log_no} {LIKE_MODULE}"


LIKE_MODULE_FALLBACK = f"div.area_sympathy.pcol2 {LIKE_MODULE}"

# 공감 클릭이 실제로 부르는 API (실측 2026-09-10):
#   https://apis.naver.com/blogserver/like/v1/services/BLOG/contents/
#       {blogId}_{logNo}?suppress_response_codes=true&_method=POST&pool=blogid&callback=...
# suppress_response_codes=true 때문에 HTTP 상태는 실패해도 200이다. 진짜
# 결과는 JSONP 본문 안에 있다:
#   /**/jQuery32108881766913008888_1789038670379({"statusCode":401,
#    "errorCode":4010,"message":"로그인 하신 후 이용해 주시기 바랍니다.",...})
LIKE_API_MARK = "/like/v1/services/"
LIKE_API_TIMEOUT_MS = 5_000
LIKE_API_POLL_INTERVAL_S = 0.1
_STATUS_CODE_RE = re.compile(r'"statusCode"\s*:\s*(\d+)')


def is_like_api_url(url: str, blog_id: str, log_no: str) -> bool:
    """이 글의 공감 등록 요청인가.

    글 번호까지 맞춰 보는 이유는 한 페이지에 다른 글의 공감 위젯이 함께
    딸려 올 수 있기 때문이다 — 남의 요청 결과를 우리 결과로 읽으면 안 된다.
    """
    return (
        LIKE_API_MARK in url
        and f"contents/{blog_id}_{log_no}" in url
        and "_method=POST" in url
    )


def parse_like_api_status(body: str) -> int | None:
    """JSONP 본문에서 statusCode를 꺼낸다. 없으면 None.

    본문 형식 전체를 파싱하지 않는다 — 콜백 이름도 필드 구성도 네이버 것이라
    언제든 바뀐다 (레거시 결함 2). 필요한 것은 숫자 하나뿐이다.
    """
    match = _STATUS_CODE_RE.search(body)
    return int(match.group(1)) if match else None


def classify_like_api_status(status: int | None) -> LikeOutcome | None:
    """공감 API가 답한 statusCode를 결과로 옮긴다. None이면 '거부는 없었다'.

    이 함수는 **실패 탐지 전용**이다. 성공 응답의 형태는 일부러 가정하지
    않는다 — 성공까지 여기서 판정하려면 네이버의 성공 본문 모양을 맞춰야
    하고, 그 추측이 빗나가면 다시 거짓 성공이 된다. 거부가 없었으면
    호출자가 DOM(aria-pressed)으로 확인한다.

    실측된 값은 401(errorCode 4010, "로그인 하신 후 이용해 주시기 바랍니다")
    뿐이다. 나머지는 계열로만 가른다.
    """
    if status is None or status < 400:
        return None
    if status == 401:
        return LikeOutcome.NOT_LOGGED_IN
    if status in (403, 429):
        return LikeOutcome.BLOCKED
    return LikeOutcome.ERROR


# 401을 받았을 때 "우리 세션이 죽었다"와 "세션은 멀쩡한데 공감만 거부됐다"는
# 대응이 정반대다. 앞이면 다시 로그인하면 되고, 뒤면 다시 로그인해도 아무
# 소용이 없다 — 계정 쪽 제한이라 사람이 네이버에서 풀어야 한다. 구분하지
# 않으면 운영자는 효과 없는 재로그인을 반복한다 (2026-09-11~12에 실제로
# 그 고리에 갇혔다: blog.naver.com에서는 세션이 살아 있다고 확인되는데
# 공감만 401이었다).
API_REJECT_SESSION_DEAD = "공감 API 401 · 세션 만료 — 다시 로그인"
API_REJECT_SESSION_ALIVE = "공감 API 401 · 세션은 살아 있음 — 계정 제한 의심"


def classify_rejection(
    status: int | None, *, session_alive: bool
) -> tuple[LikeOutcome, str] | None:
    """공감 API의 statusCode와 세션 생존 여부로 거부 원인을 가른다. 순수 함수.

    거부가 없으면 None — 호출자가 DOM으로 성공을 확인한다 (성공 응답의 모양을
    가정하지 않는다는 규칙 그대로).
    """
    outcome = classify_like_api_status(status)
    if outcome is None:
        return None
    if outcome is LikeOutcome.NOT_LOGGED_IN:
        return (
            (LikeOutcome.NOT_LOGGED_IN, API_REJECT_SESSION_DEAD)
            if not session_alive
            else (LikeOutcome.BLOCKED, API_REJECT_SESSION_ALIVE)
        )
    return outcome, ""


async def session_still_alive(page: Page) -> bool:
    """이 탭의 세션이 아직 살아 있는가 — engine.session._is_logged_in과 같은 신호.

    로그인 필수 페이지를 열어 보고 nid 로그인 폼으로 튕기면 죽은 것이다.
    실패(타임아웃 등)는 "살아 있다"로 친다: 확신이 없을 때 "세션이 죽었다"고
    단정하면 멀쩡한 세션을 두고 재로그인을 시키게 되고, 그것이 바로
    계정 보호조치를 부르는 행동이다 (CLAUDE.md).
    """
    try:
        await page.goto(
            LOGIN_PROBE_URL,
            wait_until="domcontentloaded",
            timeout=LOGIN_PROBE_TIMEOUT_MS,
        )
    except (PlaywrightTimeout, PlaywrightError):
        return True
    return LOGIN_HOST not in page.url


FRAME = "#mainFrame"
GOTO_TIMEOUT_MS = 15_000
BUTTON_TIMEOUT_MS = 6_000

# click()은 액션 가능성(보임 · 가려지지 않음)만 기다릴 뿐, 그 클릭이
# 일으킨 AJAX 응답은 기다리지 않는다. 공감은 AJAX 호출이고 class가
# off→on으로 바뀌는 시점은 응답이 돌아온 뒤이므로, click() 직후 한 번만
# 읽으면 거의 항상 아직 off를 읽는다 — 실제로는 성공한 공감이 ERROR로
# 오판된다. 그래서 클릭 후에는 상태가 바뀔 때까지 짧게 폴링한다.
LIKE_VERIFY_TIMEOUT_MS = 4_000
LIKE_VERIFY_POLL_INTERVAL_S = 0.2

# 같은 TIMEOUT이라도 어디서 났느냐에 따라 대응이 정반대다 — 페이지 로딩이면
# 타임아웃을 늘리는 문제이고, 클릭 후 확인이면 네이버가 공감을 받지 않는다는
# 뜻이라 오히려 멈추는 게 맞다. 안전장치가 실행을 멈췄을 때 이 값이 없으면
# 무엇을 고쳐야 하는지 알 수 없다 (레거시 결함 9).
STAGE_GOTO = "페이지 로딩"
STAGE_FIND = "버튼 탐색"
STAGE_STATE = "버튼 상태 읽기"
STAGE_SCROLL = "버튼까지 스크롤"
STAGE_CLICK = "클릭"
STAGE_CONFIRM = "클릭 후 공감 확인"
# 위젯이 서버 상태를 채우기 전에는 무엇을 읽어도 껍데기다.
STAGE_WIDGET = "공감 위젯 초기화"
# 네이버가 공감 요청을 거부했다. 클릭까지는 정상이었다는 뜻이라 앞 단계와
# 구분해야 한다 — 여기서 401이 나오면 고칠 것은 셀렉터가 아니라 로그인이다.
STAGE_API = "공감 API 거부"
# 글 번호로 좁힌 셀렉터가 빗나가 폴백으로 잡았다는 표시. 네이버가 글 페이지
# 구조를 바꾸기 시작하는 초기 징후라 실패와 함께 보여야 의미가 있다.
FALLBACK_MARK = "폴백 셀렉터"
# 공감 위젯이 서버 상태를 채우기 전에 판정했다는 표시. 이 표시가 붙은
# 결과는 "이미 공감함"을 구분하지 못한 채 나온 것이라 그만큼 덜 믿어야 한다.
WIDGET_MARK = "위젯 초기화 전"


def stage_detail(stage: str, exc: BaseException | None = None) -> str:
    """진단 문구를 만든다 — 단계 이름과, 있으면 예외의 *유형*까지만.

    예외 메시지 본문은 절대 싣지 않는다. storage_state가 예외 메시지에 실린
    적이 있고(보안 규칙), 이 값은 화면과 실행 로그 파일에 그대로 남는다.
    """
    if exc is None:
        return stage
    return f"{stage} ({type(exc).__name__})"


def post_url(blog_id: str, log_no: str) -> str:
    return f"https://blog.naver.com/{blog_id}/{log_no}"


def classify_button_state(aria_pressed: str | None) -> LikeOutcome | None:
    """공감 버튼의 aria-pressed에서 상태를 읽는다.

    None을 반환하면 '아직 누르지 않았고 누를 수 있다'는 뜻이다.

    **class의 on/off 토큰을 보지 않는다.** 2026-09-10 실측: 이 버튼은
    aria-haspopup="true"인 리액션 레이어 열기 버튼이라, 레이어가 열리거나
    아이콘 애니메이션이 도는 동안에도 class에 `on`이 붙는다. 공감이 401로
    거부된 시도 21건이 전부 그 `on`을 보고 SUCCESS로 기록됐다. aria-pressed는
    위젯이 서버 상태를 받아 채우는 값이라 그런 식으로 흔들리지 않는다.
    """
    if aria_pressed == "true":
        return LikeOutcome.ALREADY_LIKED
    if aria_pressed == "false":
        return None
    # 속성 자체가 없다 = 위젯 구조가 바뀌었다. 조용히 넘기지 않는다.
    return LikeOutcome.ERROR


def is_confirmed_liked(aria_pressed: str | None) -> bool:
    """클릭 후 실제로 '눌림' 상태가 됐는지 판정한다. 순수 술어."""
    return classify_button_state(aria_pressed) is LikeOutcome.ALREADY_LIKED


async def press_like(
    page: Page, blog_id: str, log_no: str, *, dry_run: bool = False
) -> LikeResult:
    """dry_run=True일 때의 SUCCESS는 '눌렀다'가 아니라 '버튼을 찾았고 누를 수
    있었다'는 뜻이다 — 실제로 클릭하지 않으므로 진짜 공감과는 구분해서 읽어야
    한다. 호출자가 dry_run을 넘겼으므로 그 사실은 이미 알고 있다.
    """
    try:
        await page.goto(
            post_url(blog_id, log_no),
            wait_until="domcontentloaded",
            timeout=GOTO_TIMEOUT_MS,
        )
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, stage_detail(STAGE_GOTO))
    except PlaywrightError as exc:
        # DNS 실패, 연결 리셋, 페이지 크래시 등. 이 예외를 흘려보내면 실행
        # 전체가 죽는다 — 수백 개 블로그를 몇 시간 도는 동안 일시적 네트워크
        # 오류는 사실상 확실히 일어난다.
        return LikeResult(LikeOutcome.ERROR, stage_detail(STAGE_GOTO, exc))

    if LOGIN_HOST in page.url:
        return LikeResult(LikeOutcome.NOT_LOGGED_IN)

    frame = page.frame_locator(FRAME)
    button = None
    used_fallback = False
    for index, selector in enumerate((like_button_selector(log_no), LIKE_BUTTON_FALLBACK)):
        candidate = frame.locator(selector).first
        try:
            await candidate.wait_for(state="attached", timeout=BUTTON_TIMEOUT_MS)
        except PlaywrightTimeout:
            continue    # 스킨에 따라 id가 없을 수 있다 — 다음 셀렉터로
        except PlaywrightError as exc:
            return LikeResult(LikeOutcome.ERROR, stage_detail(STAGE_FIND, exc))
        button = candidate
        used_fallback = index == 1
        break
    if button is None:
        return LikeResult(LikeOutcome.NO_BUTTON, stage_detail(STAGE_FIND))

    def detail(stage: str, exc: BaseException | None = None) -> str:
        text = stage_detail(stage, exc)
        return f"{text} · {FALLBACK_MARK}" if used_fallback else text

    def ok_detail() -> str:
        """성공한 시도에는 단계 이름을 남기지 않는다 — 진단할 것이 없는데
        모든 성공 줄에 문구가 붙으면 정작 실패 줄이 묻힌다. 폴백을 썼다는
        사실만은 성공이어도 남긴다 (구조 변화의 초기 징후다)."""
        return FALLBACK_MARK if used_fallback else ""

    # 스크롤이 먼저다. 본문 안 버튼은 글 아래쪽(실측 y=7,000~22,000px)에 있어
    # 처음에는 화면 밖이고, 스크롤하지 않고 클릭하면 "element is outside of the
    # viewport"로 타임아웃난다. 그리고 공감 위젯은 화면에 들어온 뒤에야
    # 초기화되므로, 스크롤은 상태를 읽기 위한 전제이기도 하다.
    try:
        await button.scroll_into_view_if_needed(timeout=BUTTON_TIMEOUT_MS)
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_SCROLL))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_SCROLL, exc))

    # 위젯이 서버 상태를 받아 채울 때까지 기다린다. 이걸 기다리지 않으면
    # 정적 마크업의 껍데기(언제나 off · aria-pressed=false · 카운트 0)를 읽게
    # 되어 이미 공감한 글도 "안 눌림"으로 보이고, 클릭은 아직 스크립트가 붙지
    # 않은 <a onclick="return false">에 떨어져 아무 일도 일어나지 않는다.
    #
    # 초기화가 끝나지 않아도 멈추지는 않는다 — data-loaded는 네이버의
    # 마크업이라 언제든 바뀐다(레거시 결함 2). 대신 그 사실을 detail에 남기고
    # 진행한다. 클릭이 실제로 먹었는지는 아래에서 공감 API가 직접 답한다.
    widget_ready = await _wait_for_widget(frame, log_no, used_fallback)
    stale = "" if widget_ready else f" · {WIDGET_MARK}"

    try:
        state = classify_button_state(await button.get_attribute("aria-pressed"))
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_STATE))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_STATE, exc))
    if state is LikeOutcome.ALREADY_LIKED:
        # 이미 눌려 있으면 절대 다시 클릭하지 않는다 — 이 버튼은 토글이라
        # 한 번 더 누르면 공감이 **취소된다**. 초기화 전이라 이 값을 덜
        # 믿어야 하는 경우에도 마찬가지다: 잘못 건너뛰면 공감 하나를 놓칠
        # 뿐이지만, 잘못 누르면 이미 얻은 답방 기회를 되돌린다.
        return LikeResult(state, (ok_detail() + stale).lstrip(" ·"))
    if state is LikeOutcome.ERROR:
        # aria-pressed가 아예 없다 — 위젯 구조가 바뀌었다는 뜻이라 단계를 남긴다.
        return LikeResult(state, detail(STAGE_STATE) + stale)

    if dry_run:
        # 드라이런: 버튼을 찾고 누를 수 있는 데까지만. 클릭하지 않는다.
        return LikeResult(LikeOutcome.SUCCESS, (ok_detail() + stale).lstrip(" ·"))

    # 클릭이 부르는 공감 API의 응답을 붙잡는다. 이것이 유일하게 권위 있는
    # 신호다 — DOM은 레이어가 열리기만 해도 눌린 것처럼 보이지만, 네이버가
    # 401로 거부하면 API는 거부라고 말한다 (실측 2026-09-10: 거짓 성공 21건).
    watcher = _LikeApiWatcher(page, blog_id, log_no)
    with watcher:
        try:
            await button.click(timeout=BUTTON_TIMEOUT_MS)
        except PlaywrightTimeout:
            return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CLICK) + stale)
        except PlaywrightError as exc:
            return LikeResult(LikeOutcome.ERROR, detail(STAGE_CLICK, exc) + stale)

        status = await watcher.status()

    if classify_like_api_status(status) is not None:
        # 401이면 세션이 정말 죽었는지 먼저 물어본다. 실패 경로에서만 드는
        # 비용이고, 그 한 번이 "재로그인하면 되는 문제"와 "재로그인해도
        # 소용없는 문제"를 가른다.
        alive = await session_still_alive(page)
        rejected, cause = classify_rejection(status, session_alive=alive)
        why = f"{detail(STAGE_API)} · {cause}" if cause else detail(STAGE_API)
        return LikeResult(rejected, why + stale)

    # 거부는 없었다. 이제 DOM으로 확인한다 — 성공 응답의 형태를 가정하지
    # 않기 위해서다. 여기서 나는 TIMEOUT은 "눌렀는데 반영되지 않았다"는
    # 뜻이라 로딩이 느린 것과는 대응이 정반대다.
    try:
        confirmed = await _wait_for_like_confirmation(button)
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CONFIRM) + stale)
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_CONFIRM, exc) + stale)
    if confirmed:
        return LikeResult(LikeOutcome.SUCCESS, ok_detail())
    return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CONFIRM) + stale)


async def _wait_for_like_confirmation(
    button, timeout_ms: int = LIKE_VERIFY_TIMEOUT_MS
) -> bool:
    """클릭 후 aria-pressed가 true로 바뀔 때까지 짧게 폴링한다.

    Playwright의 `expect()` 대신 수동 폴링을 쓰는 이유는 타임아웃 시 명확히
    False를 반환해 호출자가 LikeOutcome.TIMEOUT으로 사상하기 쉽게 하기
    위함이다 (예외를 잡아 유형을 다시 판별할 필요가 없다).
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        if is_confirmed_liked(await button.get_attribute("aria-pressed")):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(LIKE_VERIFY_POLL_INTERVAL_S)


async def _wait_for_widget(frame, log_no: str, used_fallback: bool) -> bool:
    """공감 위젯이 서버 상태를 채울 때까지 기다린다 (data-loaded="1").

    못 기다렸으면 False. 예외를 올리지 않는다 — 이 표시는 네이버의 마크업이라
    사라질 수 있고, 그때 모든 공감이 실패해서는 안 된다. 판정의 최종 근거는
    공감 API의 응답이다.
    """
    selector = LIKE_MODULE_FALLBACK if used_fallback else like_module_selector(log_no)
    module = frame.locator(selector).first
    deadline = time.monotonic() + (WIDGET_READY_TIMEOUT_MS / 1000)
    while True:
        try:
            if await module.get_attribute("data-loaded") == "1":
                return True
        except PlaywrightError:
            return False
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(WIDGET_POLL_INTERVAL_S)


class _LikeApiWatcher:
    """이 글의 공감 API 응답 하나를 붙잡는다.

    클릭 **전에** 붙여야 한다. 클릭 후에 기다리기 시작하면 이미 지나간 응답을
    놓친다. 응답 본문은 나중에 읽어도 되므로, 이벤트 콜백에서는 Response
    객체만 모으고 판독은 status()에서 한다.
    """

    def __init__(self, page: Page, blog_id: str, log_no: str) -> None:
        self._page = page
        self._blog_id = blog_id
        self._log_no = log_no
        self._seen: list = []
        self._handler = None

    def __enter__(self) -> "_LikeApiWatcher":
        def on_response(response) -> None:
            if is_like_api_url(response.url, self._blog_id, self._log_no):
                self._seen.append(response)

        self._handler = on_response
        self._page.on("response", on_response)
        return self

    def __exit__(self, *_exc) -> bool:
        if self._handler is not None:
            self._page.remove_listener("response", self._handler)
            self._handler = None
        return False

    async def status(self, timeout_ms: int = LIKE_API_TIMEOUT_MS) -> int | None:
        """공감 API가 답한 statusCode. 응답이 없었으면 None.

        응답이 없다는 것은 클릭이 요청을 만들지 못했다는 뜻이다 (위젯이 아직
        붙지 않았을 때가 그렇다). 그 경우도 성공이 아니지만, 여기서 단정하지
        않고 호출자가 DOM 확인으로 넘어가게 둔다.
        """
        deadline = time.monotonic() + (timeout_ms / 1000)
        while True:
            if self._seen:
                try:
                    return parse_like_api_status(await self._seen[0].text())
                except PlaywrightError:
                    return None
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(LIKE_API_POLL_INTERVAL_S)
