"""층 2+3 — 실제 네이버 로그인 페이지의 셀렉터가 아직 유효한지 확인한다.

자격증명을 쓰지 않는다. 페이지를 열어 입력칸과 로그인 버튼이 각각 하나씩
보이는지만 본다. 계정 위험은 없다.

이 테스트가 없어서 `.btn_login`이 사라진 것을 실행 시점(로그인 30초 타임아웃)
에야 알았다 — 레거시 결함 3의 재발 경로다.

    python -m pytest -m browser -v
"""
from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from engine.session import LOGIN_BUTTON, LOGIN_ID, LOGIN_PW, LOGIN_URL

pytestmark = [pytest.mark.browser, pytest.mark.contract]


@pytest.mark.parametrize("selector", [LOGIN_ID, LOGIN_PW, LOGIN_BUTTON])
async def test_login_page_selector_still_matches_exactly_one_visible_element(selector):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            count = await page.locator(selector).locator("visible=true").count()
        finally:
            await browser.close()

    assert count == 1, (
        f"로그인 페이지 셀렉터 {selector!r}가 보이는 요소 {count}개와 일치합니다 "
        "— 네이버 로그인 페이지 구조가 바뀌었습니다."
    )
