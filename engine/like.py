"""공감 클릭.

결과를 유형으로 반환한다. 레거시는 이 일곱 가지를 "공감 없음 or 이미 함" 한
줄로 뭉갰다. 유형을 나누는 이유는 표시가 아니라 대응이 각각 다르기 때문이다
(스펙 §7.1).

클릭 후 상태가 실제로 바뀌었는지 확인한다. 확인 없는 클릭은 거짓 성공을 만들고,
그러면 차단 감지가 무력해진다.
"""
from __future__ import annotations

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from engine.models import LikeOutcome
from engine.session import LOGIN_HOST

LIKE_BUTTON = "a.u_likeit_list_btn"
FRAME = "#mainFrame"
GOTO_TIMEOUT_MS = 15_000
BUTTON_TIMEOUT_MS = 6_000


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


async def press_like(
    page: Page, blog_id: str, log_no: str, *, dry_run: bool = False
) -> LikeOutcome:
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
        return LikeOutcome.TIMEOUT
    except PlaywrightError:
        # DNS 실패, 연결 리셋, 페이지 크래시 등. 이 예외를 흘려보내면 실행
        # 전체가 죽는다 — 수백 개 블로그를 몇 시간 도는 동안 일시적 네트워크
        # 오류는 사실상 확실히 일어난다.
        return LikeOutcome.ERROR

    if LOGIN_HOST in page.url:
        return LikeOutcome.NOT_LOGGED_IN

    try:
        button = page.frame_locator(FRAME).locator(LIKE_BUTTON).first
        await button.wait_for(state="attached", timeout=BUTTON_TIMEOUT_MS)
    except PlaywrightTimeout:
        return LikeOutcome.NO_BUTTON
    except PlaywrightError:
        return LikeOutcome.ERROR

    try:
        state = classify_button_state(await button.get_attribute("class"))
        if state is not None:
            return state          # ALREADY_LIKED 또는 ERROR

        if dry_run:
            # 드라이런: 버튼을 찾는 데까지만. 클릭하지 않는다.
            return LikeOutcome.SUCCESS

        await button.click(timeout=BUTTON_TIMEOUT_MS)

        # 클릭이 실제로 반영됐는지 확인한다.
        after = classify_button_state(await button.get_attribute("class"))
        if after is LikeOutcome.ALREADY_LIKED:
            return LikeOutcome.SUCCESS
        return LikeOutcome.ERROR
    except PlaywrightTimeout:
        return LikeOutcome.TIMEOUT
    except PlaywrightError:
        return LikeOutcome.ERROR
