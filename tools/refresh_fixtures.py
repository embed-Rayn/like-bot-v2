"""실측 응답을 tests/fixtures/에 저장한다. 손으로 지어낸 샘플보다 정확하다.

  python tools/refresh_fixtures.py
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://section.blog.naver.com/Search/Post.naver"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    return urllib.request.urlopen(req, timeout=20).read()


def _search(page: int) -> bytes:
    q = urllib.parse.urlencode({
        "countPerPage": 7, "currentPage": page, "keyword": "헬스장", "type": "post",
        "orderBy": "sim", "rangeType": "ALL", "startDate": "", "endDate": "",
    })
    return _get(f"https://section.blog.naver.com/ajax/SearchList.naver?{q}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    page1 = _search(1)
    (OUT / "search_page1.json").write_bytes(page1)

    # 144페이지는 0건 — 종료 판정 테스트용
    (OUT / "search_empty.json").write_bytes(_search(144))

    # page1의 첫 블로그로 RSS 픽스처를 만든다
    body = page1.decode("utf-8").split("\n", 1)[1]
    blog_id = json.loads(body)["result"]["searchList"][0]["domainIdOrBlogId"]
    (OUT / "rss_sample.xml").write_bytes(_get(f"https://rss.blog.naver.com/{blog_id}.xml"))

    print(f"저장 완료: {OUT}  (rss 대상 blog_id={blog_id})")


if __name__ == "__main__":
    main()
