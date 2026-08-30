"""블로그 검색 — 응답 파서(순수)와 페이지네이션(HTTP).

엔드포인트와 응답 구조는 2026-08-30 실측 결과다. 스펙 §3.1 참조.
파서를 순수 함수로 분리해 두었기 때문에 브라우저도 네트워크도 없이 테스트된다.
"""
from __future__ import annotations

import json

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
