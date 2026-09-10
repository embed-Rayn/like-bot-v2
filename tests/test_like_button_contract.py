"""층 2+3 — 실제 글 페이지에서 공감 버튼 셀렉터가 아직 유효한지 확인한다.

로그인하지 않는다. 검색 API로 오늘 글 하나를 찾아 열고, 버튼이 있는지만 본다.
클릭하지 않으므로 계정 위험은 없다.

레거시 결함 2(셀렉터가 조용히 썩는 것)의 재발을 막는 조기 경보다 — 실제로
2026-08-31에 `a.u_likeit_list_btn`이 `a.u_likeit_button._face`로 바뀌어 있었고,
실행 시점에 no_button으로만 드러났다.

    python -m pytest -m browser -v
"""
from __future__ import annotations

import httpx
import pytest
from playwright.async_api import async_playwright

from engine.like import (
    FRAME,
    LIKE_BUTTON,
    like_button_selector,
    like_module_selector,
    post_url,
)
from engine.search import SearchClient

pytestmark = [pytest.mark.browser, pytest.mark.contract]


async def test_like_button_selector_still_matches_on_a_real_post():
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page_result = await SearchClient(http).fetch_page("헬스장", "", "", 1)
    assert page_result.items, "검색 결과가 비어 있어 확인할 글이 없습니다."
    target = page_result.items[0]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await page.goto(
                post_url(target.blog_id, target.log_no),
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await page.wait_for_timeout(2_500)
            scoped = like_button_selector(target.log_no)
            count = await page.frame_locator(FRAME).locator(scoped).count()
            loose = await page.frame_locator(FRAME).locator(LIKE_BUTTON).count()
        finally:
            await browser.close()

    assert count == 1, (
        f"{target.blog_id}/{target.log_no} 에서 글 번호로 좁힌 공감 버튼이 "
        f"{count}개입니다 (좁히지 않으면 {loose}개) — 페이지 구조가 바뀌었습니다. "
        "플로팅 버튼을 잡으면 클릭이 뷰포트 밖 타임아웃으로 실패합니다."
    )


async def test_like_widget_still_exposes_its_ready_flag_and_pressed_state():
    """2026-09-10 실측한 두 가지가 아직 유효한지 감시한다.

    1. 공감 위젯은 **지연 초기화**된다. 정적 마크업은 언제나 off · 카운트 0인
       껍데기이고, 스크롤해서 화면에 들어온 뒤 `.u_likeit_list_module` 에
       `data-loaded="1"` 이 붙으면서 서버의 진짜 상태가 채워진다.
    2. 상태는 `aria-pressed` 로 읽는다. class의 `on` 토큰은 리액션 레이어가
       열리기만 해도 붙어서 신뢰할 수 없다 — 거짓 성공 21건의 원인이었다.

    이 둘 중 하나라도 사라지면 공감 판정이 다시 조용히 틀려지므로, 실행이
    아니라 여기서 먼저 깨져야 한다. 클릭하지 않으므로 계정 위험은 없다.
    """
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page_result = await SearchClient(http).fetch_page("헬스장", "", "", 1)
    assert page_result.items, "검색 결과가 비어 있어 확인할 글이 없습니다."
    target = page_result.items[0]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await page.goto(
                post_url(target.blog_id, target.log_no),
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            frame = page.frame_locator(FRAME)
            button = frame.locator(like_button_selector(target.log_no)).first
            await button.wait_for(state="attached", timeout=8_000)
            await button.scroll_into_view_if_needed(timeout=8_000)

            module = frame.locator(like_module_selector(target.log_no)).first
            loaded = None
            for _ in range(40):
                loaded = await module.get_attribute("data-loaded")
                if loaded == "1":
                    break
                await page.wait_for_timeout(250)
            pressed = await button.get_attribute("aria-pressed")
        finally:
            await browser.close()

    assert loaded == "1", (
        f"{target.blog_id}/{target.log_no}: 스크롤 후에도 공감 위젯에 "
        f"data-loaded=1이 붙지 않았습니다 (지금 값 {loaded!r}). 초기화 표시가 "
        "바뀌었다면 위젯이 준비됐는지 알 수 없고, 준비 전 상태는 언제나 "
        "'안 눌림'으로 보입니다."
    )
    assert pressed in ("true", "false"), (
        f"공감 버튼의 aria-pressed가 {pressed!r} 입니다 — 상태를 읽을 곳이 "
        "사라졌습니다. class의 on 토큰으로 되돌아가면 안 됩니다 (레이어가 "
        "열리기만 해도 붙습니다)."
    )
