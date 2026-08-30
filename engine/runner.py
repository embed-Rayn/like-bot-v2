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
    FallbackUsed,
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
        self._producer_failed = False

    def request_stop(self) -> None:
        self._stop.set()

    # ---------------- 생산자 ----------------

    async def _produce(self, keyword: str) -> None:
        query = self._config.search_query(keyword)
        try:
            self._emit(WorkerStarted(keyword))
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
                    dropped=page.dropped,
                ))
                # MINOR: SearchPage.dropped는 지금까지 아무도 읽지 않았다.
                # 7건 중 3건이 필수 필드 누락으로 못 쓰게 됐어도 완전히
                # 조용했다 (전부 걸러진 경우만 SearchParseError로 올라온다).
                # 부분적으로 걸러지는 것도 눈에 띄어야 네이버 응답 형식이
                # 바뀌기 시작하는 초기 징후를 놓치지 않는다.
                if page.dropped:
                    self._emit(LogLine(
                        keyword,
                        f"{page_no}페이지: {page.dropped}건이 형식 오류로 제외됨",
                    ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Fix 3: 검색 실패를 조용히 삼키지 않는다. 모든 키워드의 검색이
            # 이렇게 실패하면 소비자는 빈 큐만 보고 "exhausted"로 끝나는데,
            # 그건 "검색했더니 결과가 없었다"와 구분되지 않는다 — 레거시가
            # 침묵 속에 죽은 바로 그 모양이다. run()이 요약을 만들 때 이
            # 플래그를 보고 stop_reason을 "error"로 승격한다.
            self._producer_failed = True
            self._emit(LogLine(keyword, f"검색 중 오류: {exc}"))
            self._emit(Aborted(f"'{keyword}' 검색이 실패했습니다: {exc}"))

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
            # I2: PostsClient는 모든 httpx.HTTPError를 빈 리스트로 삼킨다.
            # 이 폴백을 조용히 쓰면, 네이버가 RSS를 막거나 UA를 거부하기
            # 시작하는 순간 모든 블로그가 3개에서 1개로 조용히 줄어들고
            # 아무 데도 표시되지 않는다 — 레거시가 죽은 것과 같은 침묵
            # 저하 모양이다 (스펙 §7.4). 폴백이 쓰였다는 사실 자체를
            # 이벤트로 방출한다.
            self._emit(FallbackUsed(
                "posts",
                f"{target.blog_id}: RSS에서 최신 글을 가져오지 못해 검색 결과의 "
                "글 1건으로만 진행합니다.",
            ))
            log_nos = [target.seed_log_no]

        ok = tried = 0
        for log_no in log_nos[: self._config.likes_per_blog]:
            outcome = await self._attempt_like(target, log_no)
            tried += 1
            self._likes_tried += 1
            self._emit(LikeResultEvent(target.keyword, target.blog_id, log_no, outcome.value))

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

    async def _attempt_like(self, target: Target, log_no: str) -> LikeOutcome:
        """공감 글 하나를 시도한다.

        I6/스펙 §7.1: TIMEOUT은 딱 한 번 재시도하고, 그래도 실패하면 이
        글만 건너뛴다. 재시도는 새 요청이므로 속도 제한 토큰을 다시
        받는다. 첫 시도가 TIMEOUT이었다가 재시도로 회복된 경우, 호출자
        (그리고 차단 감지기 · 이벤트 로그)는 최종 결과만 보게 된다 — 잠깐
        느렸던 글 하나가 연속 실패 카운터를 불필요하게 갉아먹지 않는다.
        """
        await self._limiter.acquire()
        outcome = await self._like(target.blog_id, log_no)
        if outcome is LikeOutcome.TIMEOUT:
            await self._limiter.acquire()
            outcome = await self._like(target.blog_id, log_no)
        return outcome

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
            self._run_id, self._config.account, self._config.keywords,
            dry_run=self._config.dry_run,
        )

        producers = [
            asyncio.create_task(self._produce(k)) for k in self._config.keywords
        ]
        consumer = asyncio.create_task(self._consume())

        async def close_queue_when_producers_done() -> None:
            await asyncio.gather(*producers, return_exceptions=True)
            await self._queue.put(_SENTINEL)

        closer = asyncio.create_task(close_queue_when_producers_done())

        # R13 / Fix 1: 소비자가 예상 못한 예외로 죽거나 run() 자체가 밖에서
        # 취소되어도 그 시점까지의 집계는 보고되어야 한다 (결함 6의 또 다른
        # 문). 취소도 여느 예외와 같은 값으로 다룬다 — finally에서 프로듀서
        # *와 소비자*를 모두 정리한 뒤(취소된 run()이 뭔가를 남기고 반환하지
        # 않도록) 요약을 딱 한 번 만들고, 원래 있었던 예외를 다시 던진다.
        # UI는 집계와 (트레이스백이든 취소든) 둘 다 봐야 한다.
        error: BaseException | None = None
        try:
            await consumer
        except asyncio.CancelledError as exc:
            error = exc
        except Exception as exc:
            error = exc
        finally:
            for task in producers:
                task.cancel()
            consumer.cancel()
            closer.cancel()
            await asyncio.gather(*producers, consumer, closer, return_exceptions=True)

        if isinstance(error, asyncio.CancelledError):
            # 취소는 운영자가 멈춰달라고 한 것과 같은 뜻이다.
            self._stop_reason = "user"
        elif error is not None:
            self._stop_reason = "error"
        elif self._stop.is_set() and self._stop_reason == "exhausted":
            self._stop_reason = "user"
        elif self._producer_failed and self._stop_reason == "exhausted":
            # Fix 3: 정상적으로 소진된 것처럼 보이지만 검색 자체가 실패했다면
            # "결과 없음"과 구분되게 승격한다. 일부 키워드만 실패해도 같은
            # 규칙을 적용한다 — 조기 종료는 시키지 않고, 요약만 정직해진다.
            self._stop_reason = "error"

        summary = RunSummary(
            run_id=self._run_id,
            blogs_done=self._blogs_done,
            likes_ok=self._likes_ok,
            likes_tried=self._likes_tried,
            stop_reason=self._stop_reason,
            per_keyword=dict(self._per_keyword),
            dry_run=self._config.dry_run,
        )
        self._history.finish_run(
            self._run_id, summary.blogs_done, summary.likes_ok, summary.stop_reason
        )
        self._emit(RunFinished(summary))

        if error is not None:
            raise error

        return summary
