"""생산자/소비자 오케스트레이션.

키워드마다 생산자 코루틴 하나가 검색 결과를 큐에 넣고, 소비자 코루틴 하나가
브라우저 탭 하나로 공감한다. 공감은 계정 단위 속도 제한에 묶이므로 소비자를
늘려도 총량이 늘지 않는다 (결정 4).

집계는 RunSummary 하나에 누적되고, 어떤 경로로 끝나든 그 값이 보고된다.
레거시처럼 return 0, 0, 0 하는 경로가 없다 (결함 6). 로그인 세션이 끊긴
경우(R3)와 예기치 못한 예외(R13)도 같은 원칙을 따른다 — 후자는 집계를
보고한 뒤 예외를 다시 던진다.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from engine.config import RunConfig
from engine.events import (
    Aborted,
    BlogVisited,
    Event,
    LikeResultEvent,
    LogLine,
    PageCollected,
    RunFinished,
    RunSummary,
    WorkerStarted,
)
from engine.history import History
from engine.models import LikeOutcome, Target
from engine.ratelimit import RateLimiter
from engine.safety import BlockDetector

LikeFn = Callable[[str, str], Awaitable[LikeOutcome]]

QUEUE_MAXSIZE = 50
_SENTINEL = object()


class Runner:
    def __init__(
        self,
        *,
        config: RunConfig,
        history: History,
        search,
        posts,
        like_fn: LikeFn,
        limiter: RateLimiter,
        detector: BlockDetector,
        emit: Callable[[Event], None],
        run_id: str,
    ) -> None:
        self._config = config
        self._history = history
        self._search = search
        self._posts = posts
        self._like = like_fn
        self._limiter = limiter
        self._detector = detector
        self._emit = emit
        self._run_id = run_id

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._seen: set[str] = set()
        self._stop = asyncio.Event()
        self._blogs_done = 0
        self._likes_ok = 0
        self._likes_tried = 0
        self._per_keyword: dict[str, int] = {}
        self._stop_reason = "exhausted"

    def request_stop(self) -> None:
        self._stop.set()

    # ---------------- 생산자 ----------------

    async def _produce(self, keyword: str) -> None:
        self._emit(WorkerStarted(keyword))
        query = self._config.search_query(keyword)
        try:
            async for page_no, page in self._search.iter_pages(
                query, self._config.start_date, self._config.end_date
            ):
                if self._stop.is_set():
                    return

                queued = 0
                for item in page.items:
                    if item.blog_id in self._seen:
                        continue
                    if self._history.was_visited(self._config.account, item.blog_id):
                        continue
                    self._seen.add(item.blog_id)
                    await self._queue.put(
                        Target(item.blog_id, keyword, item.log_no)
                    )
                    queued += 1

                self._emit(PageCollected(
                    keyword=keyword, page=page_no, found=len(page.items),
                    queued=queued, total_count=page.total_count,
                ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit(LogLine(keyword, f"검색 중 오류: {exc}"))

    # ---------------- 소비자 ----------------

    async def _consume(self) -> None:
        while True:
            target = await self._queue.get()
            if target is _SENTINEL:
                return
            if self._stop.is_set():
                self._stop_reason = "user"
                return

            finished = await self._visit(target)
            if finished:
                return

            if self._blogs_done >= self._config.blog_limit:
                self._stop_reason = "budget"
                return

            await self._limiter.pause_between_blogs()

    async def _visit(self, target: Target) -> bool:
        """블로그 하나를 처리한다. True를 반환하면 전체 중단이다."""
        log_nos = await self._posts.recent_log_nos(
            target.blog_id, self._config.likes_per_blog
        )
        if not log_nos:
            log_nos = [target.seed_log_no]

        ok = tried = 0
        for log_no in log_nos[: self._config.likes_per_blog]:
            await self._limiter.acquire()
            outcome = await self._like(target.blog_id, log_no)
            tried += 1
            self._likes_tried += 1
            self._emit(LikeResultEvent(target.blog_id, log_no, outcome.value))

            if outcome is LikeOutcome.SUCCESS:
                ok += 1
                self._likes_ok += 1

            # R3: 로그아웃 상태로는 절대 계속 진행하지 않는다. 카운터가 5번
            # 실패를 쌓을 때까지 기다리면 그동안 확실히 실패할 5번을 더
            # 시도하며 속도 제한 토큰을 낭비한다. 탐지기보다 먼저 끊는다.
            if outcome is LikeOutcome.NOT_LOGGED_IN:
                self._record(target, ok, tried)
                self._stop_reason = "not_logged_in"
                self._emit(Aborted("세션이 더 이상 로그인 상태가 아닙니다."))
                return True

            reason = self._detector.record(outcome)
            if reason is not None:
                self._record(target, ok, tried)
                self._stop_reason = (
                    "blocked" if outcome is LikeOutcome.BLOCKED else "error"
                )
                self._emit(Aborted(reason))
                return True

        self._record(target, ok, tried)
        return False

    def _record(self, target: Target, ok: int, tried: int) -> None:
        outcome = "liked" if ok else "no_like"
        # R10: dry run은 실제로 아무것도 누르지 않았으므로 영구 방문 이력에
        # 남기지 않는다. 남기면 다음 실제 실행이 같은 블로그를 전부
        # "이미 방문함"으로 건너뛰어 아무것도 하지 않게 된다.
        if not self._config.dry_run:
            self._history.record_visit(
                self._config.account, target.blog_id, self._run_id,
                target.keyword, ok, tried, outcome,
            )
        self._blogs_done += 1
        self._per_keyword[target.keyword] = self._per_keyword.get(target.keyword, 0) + 1
        self._emit(BlogVisited(target.keyword, target.blog_id, ok, tried))

    # ---------------- 진입점 ----------------

    async def run(self) -> RunSummary:
        self._history.start_run(
            self._run_id, self._config.account, self._config.keywords
        )

        producers = [
            asyncio.create_task(self._produce(k)) for k in self._config.keywords
        ]
        consumer = asyncio.create_task(self._consume())

        async def close_queue_when_producers_done() -> None:
            await asyncio.gather(*producers, return_exceptions=True)
            await self._queue.put(_SENTINEL)

        closer = asyncio.create_task(close_queue_when_producers_done())

        # R13: 소비자가 예상 못한 예외로 죽어도 그 시점까지의 집계는
        # 보고되어야 한다 (결함 6의 또 다른 문). finally에서 프로듀서를
        # 정리한 뒤 요약을 딱 한 번 만들고, 예외가 있었으면 보고 후 다시
        # 던진다 — UI가 집계와 트레이스백을 둘 다 봐야 한다.
        error: BaseException | None = None
        try:
            await consumer
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc
        finally:
            for task in producers:
                task.cancel()
            closer.cancel()
            await asyncio.gather(*producers, closer, return_exceptions=True)

        if error is not None:
            self._stop_reason = "error"
        elif self._stop.is_set() and self._stop_reason == "exhausted":
            self._stop_reason = "user"

        summary = RunSummary(
            run_id=self._run_id,
            blogs_done=self._blogs_done,
            likes_ok=self._likes_ok,
            likes_tried=self._likes_tried,
            stop_reason=self._stop_reason,
            per_keyword=dict(self._per_keyword),
        )
        self._history.finish_run(
            self._run_id, summary.blogs_done, summary.likes_ok, summary.stop_reason
        )
        self._emit(RunFinished(summary))

        if error is not None:
            raise error

        return summary
