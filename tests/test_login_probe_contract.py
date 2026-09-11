"""층 2+3 — 로그인 판정에 쓰는 주소가 아직 '로그인해야만 볼 수 있는 페이지'인지 본다.

`_is_logged_in()`은 LOGIN_PROBE_URL을 열어 보고, nid 로그인 폼으로 튕기면
로그아웃으로 판정한다. 이 테스트는 **로그인하지 않은 컨텍스트**로 그 주소를
열어 정말 튕기는지 확인한다. 자격증명도, 저장된 세션도 쓰지 않는다 —
계정 위험은 없다.

이게 없으면 네이버가 그 주소를 공개 페이지로 바꾸거나 없애는 날, 실행은
"로그인되어 있다"고 믿고 끝까지 돌면서 공감을 전부 401로 거부당한다 —
2026-09-10에 실제로 일어난 사고(공감 21건 전부 거부)의 모양 그대로다.

반대 방향(로그인 상태에서 그 페이지에 도착하는가)은 계정이 있어야 하므로
여기서 재지 않는다. tools/login.py로 세션을 만든 뒤 실행이 확인한다.

    python -m pytest -m browser -v
"""
from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from engine.session import LOGIN_HOST, LOGIN_PROBE_URL

pytestmark = [pytest.mark.browser, pytest.mark.contract]


async def test_probe_url_bounces_a_logged_out_visitor_to_the_login_form():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await page.goto(LOGIN_PROBE_URL, wait_until="domcontentloaded")
            landed = page.url
        finally:
            await browser.close()

    assert LOGIN_HOST in landed, (
        f"{LOGIN_PROBE_URL} 가 로그인하지 않은 방문자를 로그인 폼으로 보내지 "
        f"않았습니다 (도착: {landed}). 이 주소는 더 이상 로그인 여부를 "
        "판정할 수 없습니다 — 다른 '로그인 필수' 페이지를 찾아야 합니다."
    )


async def test_the_login_form_itself_is_not_used_as_the_probe():
    """로그인 페이지는 로그인/로그아웃 양쪽에서 똑같이 로그인 폼을 보여 준다.

    그래서 그 주소로는 판정이 불가능하다 — 2026-09-11 SessionExpired 사고.
    """
    assert LOGIN_HOST not in LOGIN_PROBE_URL
