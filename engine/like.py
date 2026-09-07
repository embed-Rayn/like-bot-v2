"""공감 클릭.

결과를 유형으로 반환한다. 레거시는 이 일곱 가지를 "공감 없음 or 이미 함" 한
줄로 뭉갰다. 유형을 나누는 이유는 표시가 아니라 대응이 각각 다르기 때문이다
(스펙 §7.1).

클릭 후 상태가 실제로 바뀌었는지 확인한다. 확인 없는 클릭은 거짓 성공을 만들고,
그러면 차단 감지가 무력해진다.
"""
from __future__ import annotations

import asyncio
import time

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from engine.models import LikeOutcome, LikeResult
from engine.session import LOGIN_HOST

# 2026-08-31 실측. 클래스가 u_likeit_list_btn → u_likeit_button _face 로 바뀌었고,
# 글 하나에 같은 버튼이 둘 렌더링된다.
#   [0] 스크롤을 따라오는 플로팅 버튼. 늘 뷰포트 바로 아래(y = 뷰포트 높이 + 6)에
#       머물러서, 뷰포트를 키우든 부모를 스크롤하든 클릭이
#       "element is outside of the viewport"로 타임아웃된다.
#   [1] 본문 안 버튼. 조상이 #area_sympathy{logNo} 이고 스크롤해서 누를 수 있다.
# 그래서 글 번호로 범위를 좁혀 본문 안 버튼만 고른다 — 같은 페이지에 딸려 오는
# 다른 글의 버튼을 잘못 누를 위험도 함께 사라진다. on/off 토큰은 그대로여서
# classify_button_state는 손대지 않는다.
LIKE_BUTTON = "a.u_likeit_button._face"
LIKE_BUTTON_FALLBACK = f"div.area_sympathy.pcol2 {LIKE_BUTTON}"


def like_button_selector(log_no: str) -> str:
    return f"#area_sympathy{log_no} {LIKE_BUTTON}"
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
# 뜻이라 오히려 멈추는 게 맞다. 5건 연속 실패로 실행이 멈췄을 때 이 값이
# 없으면 무엇을 고쳐야 하는지 알 수 없다 (레거시 결함 9).
STAGE_GOTO = "페이지 로딩"
STAGE_FIND = "버튼 탐색"
STAGE_STATE = "버튼 상태 읽기"
STAGE_SCROLL = "버튼까지 스크롤"
STAGE_CLICK = "클릭"
STAGE_CONFIRM = "클릭 후 on 확인"
# 글 번호로 좁힌 셀렉터가 빗나가 폴백으로 잡았다는 표시. 네이버가 글 페이지
# 구조를 바꾸기 시작하는 초기 징후라 실패와 함께 보여야 의미가 있다.
FALLBACK_MARK = "폴백 셀렉터"


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


def classify_button_state(class_attr: str | None) -> LikeOutcome | None:
    """공감 버튼의 class에서 상태를 읽는다.

    None을 반환하면 '아직 누르지 않았고 누를 수 있다'는 뜻이다.
    """
    if not class_attr:
        return LikeOutcome.ERROR
    tokens = class_attr.split()
    if "on" in tokens:
        return LikeOutcome.ALREADY_LIKED
    if "off" in tokens:
        return None
    return LikeOutcome.ERROR


def is_confirmed_liked(class_attr: str | None) -> bool:
    """클릭 후 버튼 class가 실제로 '눌림'(on) 상태로 바뀌었는지 판정한다.

    순수 술어 — Playwright 없이 테스트된다. classify_button_state가
    ALREADY_LIKED를 반환하는 경우만 '확인됨'으로 본다. off는 아직
    반영되지 않은 것이고, ERROR(알 수 없는 클래스 · 속성 없음)는 확인된
    상태가 아니므로 계속 대기하다 타임아웃으로 처리한다.
    """
    return classify_button_state(class_attr) is LikeOutcome.ALREADY_LIKED


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

    try:
        state = classify_button_state(await button.get_attribute("class"))
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_STATE))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_STATE, exc))
    if state is LikeOutcome.ALREADY_LIKED:
        return LikeResult(state, ok_detail())
    if state is not None:
        # class에 on도 off도 없다 — 버튼 구조가 바뀌었다는 뜻이라 단계를 남긴다.
        return LikeResult(state, detail(STAGE_STATE))

    if dry_run:
        # 드라이런: 버튼을 찾는 데까지만. 클릭하지 않는다.
        return LikeResult(LikeOutcome.SUCCESS, ok_detail())

    # 본문 안 버튼은 글 아래쪽에 있어 처음에는 화면 밖이다. 스크롤하지
    # 않고 클릭하면 "element is outside of the viewport"로 타임아웃난다.
    try:
        await button.scroll_into_view_if_needed(timeout=BUTTON_TIMEOUT_MS)
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_SCROLL))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_SCROLL, exc))

    try:
        await button.click(timeout=BUTTON_TIMEOUT_MS)
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CLICK))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_CLICK, exc))

    # 클릭이 실제로 반영됐는지 확인한다 — AJAX 응답을 기다리는 유일한
    # 지점이다. 확인 없는 클릭은 거짓 성공을 만들고, 그러면 차단 감지가
    # 무력해진다. 여기서 나는 TIMEOUT은 "눌렀는데 네이버가 반영하지 않았다"는
    # 뜻이라 로딩이 느린 것과는 대응이 정반대다 — 그래서 단계를 남긴다.
    try:
        confirmed = await _wait_for_like_confirmation(button)
    except PlaywrightTimeout:
        return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CONFIRM))
    except PlaywrightError as exc:
        return LikeResult(LikeOutcome.ERROR, detail(STAGE_CONFIRM, exc))
    if confirmed:
        return LikeResult(LikeOutcome.SUCCESS, ok_detail())
    return LikeResult(LikeOutcome.TIMEOUT, detail(STAGE_CONFIRM))


async def _wait_for_like_confirmation(
    button, timeout_ms: int = LIKE_VERIFY_TIMEOUT_MS
) -> bool:
    """클릭 후 버튼 class가 on으로 바뀔 때까지 짧게 폴링한다.

    Playwright의 `expect().to_have_class()` 대신 수동 폴링을 쓰는 이유는
    타임아웃 시 명확히 False를 반환해 호출자가 LikeOutcome.TIMEOUT으로
    사상하기 쉽게 하기 위함이다 (예외를 잡아 유형을 다시 판별할 필요가
    없다).
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        class_attr = await button.get_attribute("class")
        if is_confirmed_liked(class_attr):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(LIKE_VERIFY_POLL_INTERVAL_S)
