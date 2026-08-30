import httpx
import pytest

from engine.search import SearchClient, SearchParseError, build_params

FIXTURE_PAGE = (
    ")]}',\n"
    '{"result":{"totalCount":20,"pagePerCount":7,"searchList":['
    '{"domainIdOrBlogId":"blog_a","logNo":111,"title":"t","blogName":"b","addDate":1},'
    '{"domainIdOrBlogId":"blog_b","logNo":222,"title":"t","blogName":"b","addDate":2}]}}'
)
FIXTURE_EMPTY = ")]}',\n" '{"result":{"totalCount":0,"pagePerCount":7,"searchList":[]}}'
FIXTURE_UNUSABLE = (
    ")]}',\n"
    '{"result":{"totalCount":5,"pagePerCount":7,"searchList":['
    '{"title":"no ids here","blogName":"b","addDate":1},'
    '{"title":"still no ids","blogName":"b","addDate":2},'
    '{"title":"nope","blogName":"b","addDate":3}]}}'
)


def test_build_params_uses_period_range_and_recentdate():
    p = build_params("헬스장 -협찬", "2026-08-29", "2026-08-30", 3)
    assert p["keyword"] == "헬스장 -협찬"
    assert p["currentPage"] == "3"
    assert p["countPerPage"] == "7"
    assert p["orderBy"] == "recentdate"
    assert p["rangeType"] == "PERIOD"
    assert p["startDate"] == "2026-08-29"
    assert p["endDate"] == "2026-08-30"


async def test_fetch_page_sends_referer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("referer")
        return httpx.Response(200, text=FIXTURE_PAGE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        page = await SearchClient(http).fetch_page("k", "2026-08-29", "2026-08-30", 1)

    assert "section.blog.naver.com" in seen["referer"]
    assert [i.blog_id for i in page.items] == ["blog_a", "blog_b"]


async def test_iter_pages_stops_on_first_empty_page():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["currentPage"])
        calls.append(page)
        return httpx.Response(200, text=FIXTURE_PAGE if page <= 2 else FIXTURE_EMPTY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        pages = [n async for n, _ in SearchClient(http).iter_pages("k", "a", "b")]

    assert pages == [1, 2]
    assert calls == [1, 2, 3]      # 3페이지를 받아보고 비어서 멈춘다


async def test_http_error_raises():
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SearchClient(http).fetch_page("k", "a", "b", 1)


async def test_iter_pages_raises_when_rows_present_but_all_unusable():
    # Page number (2) and raw row count (3) are deliberately different values
    # so the assertion below can't pass by matching the wrong one.
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["currentPage"])
        return httpx.Response(200, text=FIXTURE_PAGE if page == 1 else FIXTURE_UNUSABLE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(SearchParseError) as exc_info:
            [n async for n, _ in SearchClient(http).iter_pages("k", "a", "b")]

    message = str(exc_info.value)
    assert "2페이지" in message
    assert "3건" in message


async def test_iter_pages_ends_cleanly_on_genuinely_empty_first_page():
    """raw_count == 0 (searchList:[]) must return quietly, not raise —
    distinct from the all-rows-unusable case above."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=FIXTURE_EMPTY))
    async with httpx.AsyncClient(transport=transport) as http:
        pages = [n async for n, _ in SearchClient(http).iter_pages("k", "a", "b")]

    assert pages == []
