"""드라이런 — 로그인하고, 글을 열고, 공감 버튼을 찾는 데까지만 한다.

클릭하지 않으므로 부작용이 없다. 셀렉터 유효성 · iframe 전환 · 로그인 생존을
계정 위험 없이 확인한다. 새 키워드를 본격 실행하기 전에 어떤 블로그가 잡히는지
미리 보는 용도로도 쓴다.

    python tools/dryrun.py <네이버ID> <키워드> [--blogs 5]
"""
from __future__ import annotations

import argparse
import asyncio

import httpx

from engine.like import press_like
from engine.models import LikeOutcome
from engine.paths import AppPaths
from engine.posts import PostsClient
from engine.ratelimit import RateLimiter
from engine.search import SearchClient
from engine.session import BrowserSession
from engine.config import DEFAULTS, default_dates

# I4: 이 도구는 운영자가 진짜 계정으로 처음 돌려 보라고 안내받는 바로 그
# 도구다. 글 로드는 여전히 로그인된 브라우저로 이루어지므로(§3.3) 페이싱이
# 없으면 계정 안전을 검증하려는 도구가 오히려 계정을 위험에 빠뜨린다.
# 상한도 없이 --blogs를 크게 넘기면 더더욱 그렇다.
MAX_BLOGS = 20


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
    # 비밀번호를 묻지 않는다 — 세션이 없으면 로그인 창이 열리고 사람이 직접 한다
    # (engine/session.py의 _login, 2026-09-11 자동 입력 폐지).
    await session.open(account, on_challenge=print)
    print("로그인 확인됨. 공감 버튼을 찾습니다 (클릭하지 않습니다).")

    # I4: 글 로드도 로그인된 브라우저로 하는 요청이므로 실제 실행과 같은
    # 속도 제한을 건다. 클릭하지 않는다는 사실이 페이싱을 면제해 주지 않는다.
    limiter = RateLimiter(DEFAULTS["likes_per_minute"])

    try:
        for blog_id, log_nos in plans:
            await limiter.acquire()
            result = await press_like(session.page, blog_id, log_nos[0], dry_run=True)
            outcome = result.outcome
            mark = "찾음" if outcome is LikeOutcome.SUCCESS else outcome.value
            print(f"  {blog_id:24s} {log_nos[0]:>14s}  {mark}")
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="like-bot-v2 드라이런")
    parser.add_argument("account")
    parser.add_argument("keyword")
    parser.add_argument(
        "--blogs", type=int, default=5,
        help=f"확인할 블로그 수 (최대 {MAX_BLOGS})",
    )
    args = parser.parse_args()
    if not 1 <= args.blogs <= MAX_BLOGS:
        parser.error(f"--blogs는 1 이상 {MAX_BLOGS} 이하여야 합니다.")
    asyncio.run(main_async(args.account, args.keyword, args.blogs))


if __name__ == "__main__":
    main()
