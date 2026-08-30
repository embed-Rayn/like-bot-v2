"""블로그 검색 — 응답 파서(순수)와 페이지네이션(HTTP).

엔드포인트와 응답 구조는 2026-08-30 실측 결과다. 스펙 §3.1 참조.
파서를 순수 함수로 분리해 두었기 때문에 브라우저도 네트워크도 없이 테스트된다.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from engine.models import SearchItem, SearchPage

JSON_PREFIX = ")]}',"


class SearchParseError(Exception):
    """검색 응답이 예상한 JSON 형태가 아니다."""


def parse_search_response(raw: str | bytes) -> SearchPage:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    text = text.lstrip()
    if text.startswith(JSON_PREFIX):
        text = text[len(JSON_PREFIX):]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SearchParseError(f"JSON 파싱 실패: {exc}") from exc

    result = payload.get("result")
    if not isinstance(result, dict):
        raise SearchParseError("응답에 result 객체가 없습니다.")

    search_list = result.get("searchList") or []
    raw_count = len(search_list)

    items = [
        SearchItem(
            blog_id=str(row.get("domainIdOrBlogId", "")).strip(),
            log_no=str(row.get("logNo", "")).strip(),
            title=str(row.get("title", "")),
            blog_name=str(row.get("blogName", "")),
            add_date_ms=int(row.get("addDate") or 0),
        )
        for row in search_list
        if str(row.get("domainIdOrBlogId", "")).strip()
        and str(row.get("logNo", "")).strip()
    ]

    return SearchPage(
        items=items,
        total_count=int(result.get("totalCount") or 0),
        per_page=int(result.get("pagePerCount") or 7),
        raw_count=raw_count,
    )


SEARCH_URL = "https://section.blog.naver.com/ajax/SearchList.naver"
SEARCH_HEADERS = {
    "Referer": "https://section.blog.naver.com/Search/Post.naver",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
PER_PAGE = 7


def build_params(query: str, start_date: str, end_date: str, page: int) -> dict[str, str]:
    return {
        "countPerPage": str(PER_PAGE),
        "currentPage": str(page),
        "keyword": query,
        "type": "post",
        "orderBy": "recentdate",
        "rangeType": "PERIOD",
        "startDate": start_date,
        "endDate": end_date,
    }


class SearchClient:
    """검색 API 호출. Referer 헤더가 없으면 응답이 달라지므로 항상 붙인다."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_page(
        self, query: str, start_date: str, end_date: str, page: int
    ) -> SearchPage:
        response = await self._http.get(
            SEARCH_URL,
            params=build_params(query, start_date, end_date, page),
            headers=SEARCH_HEADERS,
        )
        response.raise_for_status()
        return parse_search_response(response.text)

    async def iter_pages(
        self, query: str, start_date: str, end_date: str, first_page: int = 1
    ) -> AsyncIterator[tuple[int, SearchPage]]:
        """빈 페이지를 만날 때까지 페이지를 올린다 (결정 2).

        143페이지 상한을 코드에 박지 않는다. 네이버가 상한을 바꿔도 동작하고,
        좁은 키워드는 자연히 더 일찍 끝난다.

        raw_count > 0 인데 items가 비었다면(모든 행이 필수 필드 누락으로
        걸러짐) '결과 없음'이 아니라 파싱 실패다 — 조용히 멈추지 않고
        SearchParseError를 던진다 (Ruling R5). 네이버가 필드명을 바꿔도
        전체 실행이 "성공, 0건 처리"로 조용히 끝나는 일을 막기 위함이다.
        """
        page = first_page
        while True:
            result = await self.fetch_page(query, start_date, end_date, page)
            if result.raw_count > 0 and result.is_empty:
                raise SearchParseError(
                    f"{page}페이지: searchList에 {result.raw_count}건이 왔지만 "
                    "전부 필수 필드가 없어 사용할 수 없습니다."
                )
            if result.is_empty:
                return
            yield page, result
            page += 1
