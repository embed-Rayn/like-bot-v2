"""층 2 — 네이버에 실제 요청을 보낸다. 비로그인이므로 계정 위험은 없다.

네이버가 바뀌었는지 감지하는 조기 경보다. 레거시의 결함 1이 반년 넘게
방치된 이유가 이런 감지 수단의 부재였다.

    python -m pytest -m contract -v
"""
import httpx
import pytest

from engine.posts import PostsClient
from engine.search import SearchClient

pytestmark = pytest.mark.contract

KEYWORD = "헬스장"


async def test_search_api_still_returns_expected_fields():
    async with httpx.AsyncClient(timeout=20) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)

    assert page.per_page == 7, "pagePerCount가 7이 아닙니다 — 페이지 크기가 바뀌었습니다."
    assert page.items, "searchList가 비어 있습니다."
    first = page.items[0]
    assert first.blog_id and first.log_no.isdigit()


async def test_total_count_cap_is_still_1000():
    async with httpx.AsyncClient(timeout=20) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)
    assert page.total_count == 1000, (
        f"넓은 키워드의 totalCount가 {page.total_count}입니다 — 상한이 바뀌었을 수 있습니다."
    )


async def test_pagination_still_ends_after_page_143():
    async with httpx.AsyncClient(timeout=20) as http:
        client = SearchClient(http)
        last = await client.fetch_page(KEYWORD, "", "", 143)
        past = await client.fetch_page(KEYWORD, "", "", 144)

    assert not last.is_empty, "143페이지가 비었습니다 — 상한이 앞당겨졌습니다."
    assert past.is_empty, "144페이지에 결과가 있습니다 — 상한이 늘었습니다."


async def test_rss_still_yields_post_ids_for_real_blogs():
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)
        posts = PostsClient(http)
        results = [
            await posts.recent_log_nos(item.blog_id, 5) for item in page.items[:5]
        ]

    succeeded = sum(1 for r in results if r)
    assert succeeded >= 3, f"RSS 성공 {succeeded}/5 — RSS 제공이 줄었을 수 있습니다."
