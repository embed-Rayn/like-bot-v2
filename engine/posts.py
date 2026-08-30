"""블로그의 최신 글 목록 — RSS.

레거시는 PostList.naver를 브라우저로 열어 "목록열기" 버튼을 누르고 테이블
XPath를 훑었다. RSS는 같은 정보를 마크업 변경에 면역인 형태로 준다.
실측(2026-08-30): 대상 7곳 전부 성공, 13~50개 반환.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_LOG_NO = re.compile(r"/(\d+)(?:\?|$)")


def rss_url(blog_id: str) -> str:
    return f"https://rss.blog.naver.com/{blog_id}.xml"


def parse_rss_log_nos(xml: bytes | str) -> list[str]:
    """channel/item/link에서 글 번호를 최신순으로 뽑는다.

    <link>가 CDATA로 감싸여 있으므로 원문을 정규식으로 훑으면 안 된다.
    XML 파서는 CDATA를 투명하게 처리한다.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    log_nos: list[str] = []
    for item in root.iterfind("./channel/item"):
        link = (item.findtext("link") or "").strip()
        match = _LOG_NO.search(link)
        if match:
            log_nos.append(match.group(1))
    return log_nos


class PostsClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def recent_log_nos(self, blog_id: str, limit: int) -> list[str]:
        """실패는 예외가 아니라 빈 리스트다 — 호출자가 seed_log_no로 폴백한다."""
        try:
            response = await self._http.get(rss_url(blog_id), headers=RSS_HEADERS)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return parse_rss_log_nos(response.content)[:limit]
