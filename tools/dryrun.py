"""드라이런 — 로그인하고, 글을 열고, 공감 버튼을 찾는 데까지만 한다.

클릭하지 않으므로 부작용이 없다. 셀렉터 유효성 · iframe 전환 · 로그인 생존을
계정 위험 없이 확인한다. 새 키워드를 본격 실행하기 전에 어떤 블로그가 잡히는지
미리 보는 용도로도 쓴다.

    python tools/dryrun.py <네이버ID> <키워드> [--blogs 5]
"""
from __future__ import annotations

import argparse
import asyncio
import getpass

import httpx

from engine.like import press_like
from engine.models import LikeOutcome
from engine.paths import AppPaths
from engine.posts import PostsClient
from engine.search import SearchClient
from engine.session import BrowserSession
from engine.config import default_dates


async def main_async(account: str, keyword: str, blogs: int) -> None:
    start, end = default_dates()
    paths = AppPaths.for_app()
    paths.ensure()

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page = await SearchClient(http).fetch_page(keyword, start, end, 1)
        print(f"검색 '{keyword}' {start}~{end}: "
              f"{page.total_count}{'+' if page.is_capped else ''}건, "
              f"1페이지 {len(page.items)}건")

        targets = page.items[:blogs]
        posts = PostsClient(http)
        plans = [(t.blog_id, await posts.recent_log_nos(t.blog_id, 3) or [t.log_no])
                 for t in targets]

    for blog_id, log_nos in plans:
        print(f"  {blog_id:24s} 최신글 {log_nos}")

    session = BrowserSession(paths)
    await session.open(account, lambda: getpass.getpass(f"{account} 비밀번호: "))
    print("로그인 확인됨. 공감 버튼을 찾습니다 (클릭하지 않습니다).")

    try:
        for blog_id, log_nos in plans:
            outcome = await press_like(session.page, blog_id, log_nos[0], dry_run=True)
            mark = "찾음" if outcome is LikeOutcome.SUCCESS else outcome.value
            print(f"  {blog_id:24s} {log_nos[0]:>14s}  {mark}")
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="like-bot-v2 드라이런")
    parser.add_argument("account")
    parser.add_argument("keyword")
    parser.add_argument("--blogs", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main_async(args.account, args.keyword, args.blogs))


if __name__ == "__main__":
    main()
