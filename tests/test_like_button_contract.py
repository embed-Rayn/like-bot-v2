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

from engine.like import FRAME, LIKE_BUTTON, like_button_selector, post_url
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
