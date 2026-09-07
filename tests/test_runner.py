import asyncio
import sqlite3

import pytest

from engine.config import RunConfig
from engine.history import History
from engine.models import LikeOutcome, SearchItem, SearchPage
from engine.ratelimit import RateLimiter
from engine.runner import Runner, classify_visit_outcome
from engine.safety import BlockDetector


class FakeSearch:
    """키워드마다 페이지 목록을 미리 정해 둔다."""

    def __init__(self, pages_by_keyword):
        self._pages = pages_by_keyword

    async def iter_pages(self, query, start_date, end_date, first_page=1):
        for index, blog_ids in enumerate(self._pages.get(query, []), start=1):
            items = [
                SearchItem(blog_id=b, log_no=f"{i}00", title="t", blog_name="b",
                           add_date_ms=0)
                for i, b in enumerate(blog_ids, start=1)
            ]
            yield index, SearchPage(items=items, total_count=99, per_page=7,
                                     raw_count=len(items))


class FakePosts:
    def __init__(self, log_nos=("1", "2", "3")):
        self._log_nos = list(log_nos)

    async def recent_log_nos(self, blog_id, limit):
        return self._log_nos[:limit]


def _no_wait_limiter():
    async def nosleep(_seconds):
        return None

    return RateLimiter(600.0, sleeper=nosleep, clock=lambda: 0.0)


def _config(tmp_path, **over):
    raw = {
        "account": "acct_a",
        "keywords": ["kw1"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": 100,
        "likes_per_blog": 3,
        "likes_per_minute": 600,
        "dry_run": False,
    }
    raw.update(over)
    cfg, errors = RunConfig.validate(raw)
    assert errors == []
    return cfg


def _runner(tmp_path, config, search, like_fn, events=None):
    history = History(tmp_path / "h.db")
    return Runner(
        config=config,
        history=history,
        search=search,
        posts=FakePosts(),
        like_fn=like_fn,
        limiter=_no_wait_limiter(),
        detector=BlockDetector(),
        emit=(events.append if events is not None else (lambda e: None)),
        run_id="run1",
    ), history


async def _always(outcome):
    async def fn(blog_id, log_no):
        return outcome
    return fn


async def test_likes_every_blog_from_search(tmp_path):
    search = FakeSearch({"kw1": [["blog_a", "blog_b"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.blogs_done == 2
    assert summary.likes_ok == 6          # 블로그당 3개
    assert summary.stop_reason == "exhausted"


async def test_stops_at_blog_limit(tmp_path):
    search = FakeSearch({"kw1": [["b1", "b2", "b3", "b4", "b5"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, blog_limit=2), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.blogs_done == 2
    assert summary.stop_reason == "budget"


async def test_skips_blogs_already_visited_by_this_account(tmp_path):
    history = History(tmp_path / "h.db")
    history.record_visit("acct_a", "blog_a", "old", "kw1", 3, 3, "liked")
    history.close()

    search = FakeSearch({"kw1": [["blog_a", "blog_b"]]})
    runner, history2 = _runner(tmp_path, _config(tmp_path), search,
                               await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history2.close()

    assert summary.blogs_done == 1        # blog_a는 건너뛴다


def test_does_not_skip_blogs_visited_by_a_different_account(tmp_path):
    async def scenario():
        history = History(tmp_path / "h.db")
        history.record_visit("other_acct", "blog_a", "old", "kw1", 3, 3, "liked")
        history.close()

        search = FakeSearch({"kw1": [["blog_a"]]})
        runner, h2 = _runner(tmp_path, _config(tmp_path), search,
                             await _always(LikeOutcome.SUCCESS))
        summary = await runner.run()
        h2.close()
        return summary

    assert asyncio.run(scenario()).blogs_done == 1


async def test_same_blog_across_keywords_is_visited_once(tmp_path):
    search = FakeSearch({"kw1": [["shared"]], "kw2": [["shared"]]})
    config = _config(tmp_path, keywords=["kw1", "kw2"])
    runner, history = _runner(tmp_path, config, search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.blogs_done == 1


async def test_blocked_aborts_immediately_and_keeps_the_tally(tmp_path):
    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeOutcome.SUCCESS if calls["n"] < 3 else LikeOutcome.BLOCKED

    search = FakeSearch({"kw1": [["b1", "b2", "b3", "b4"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search, like_fn)
    summary = await runner.run()
    history.close()

    assert summary.stop_reason == "blocked"
    assert summary.likes_ok == 2          # 결함 6: 그때까지의 집계가 살아 있다


async def test_user_stop_preserves_the_tally(tmp_path):
    seen = {"n": 0}

    async def like_fn(blog_id, log_no):
        seen["n"] += 1
        if seen["n"] == 4:
            runner_holder["runner"].request_stop()
        return LikeOutcome.SUCCESS

    runner_holder = {}
    search = FakeSearch({"kw1": [["b1", "b2", "b3", "b4", "b5"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search, like_fn)
    runner_holder["runner"] = runner

    summary = await runner.run()
    history.close()

    # b1이 3개를 다 채우고, b2 처리 중 첫 좋아요(누적 4번째 호출)에서
    # request_stop()이 호출된다. 정지 플래그는 블로그 사이에서만 확인하므로
    # 진행 중이던 b2는 끝까지 마친다: b1(3) + b2(3) = 6개, 블로그 2개.
    # b3는 큐에서 꺼내지기도 전에 정지 플래그에 걸려 시작조차 하지 않는다.
    assert summary.stop_reason == "user"
    assert summary.likes_ok == 6
    assert summary.blogs_done == 2


async def test_already_liked_is_not_counted_as_success(tmp_path):
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.ALREADY_LIKED))
    summary = await runner.run()
    history.close()

    assert summary.likes_ok == 0
    assert summary.blogs_done == 1


async def test_visits_are_recorded_under_the_running_account(tmp_path):
    search = FakeSearch({"kw1": [["blog_a"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.SUCCESS))
    await runner.run()
    assert history.was_visited("acct_a", "blog_a") is True
    assert history.was_visited("acct_b", "blog_a") is False
    history.close()


# ---- MINOR: LikeResultEvent carries the keyword ----

async def test_like_result_event_carries_the_target_keyword(tmp_path):
    from engine.events import LikeResultEvent

    events = []
    search = FakeSearch({"kw1": [["b1"]], "kw2": [["b2"]]})
    config = _config(tmp_path, keywords=["kw1", "kw2"])
    runner, history = _runner(tmp_path, config, search,
                              await _always(LikeOutcome.SUCCESS), events)
    await runner.run()
    history.close()

    by_blog = {e.blog_id: e.keyword for e in events if isinstance(e, LikeResultEvent)}
    assert by_blog == {"b1": "kw1", "b2": "kw2"}


async def test_emits_events(tmp_path):
    from engine.events import BlogVisited, PageCollected, RunFinished

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.SUCCESS), events)
    await runner.run()
    history.close()

    kinds = {type(e) for e in events}
    assert PageCollected in kinds
    assert BlogVisited in kinds
    assert RunFinished in kinds


# ---- Controller ruling 1 (R3): NOT_LOGGED_IN aborts immediately ----

async def test_not_logged_in_aborts_immediately(tmp_path):
    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeOutcome.NOT_LOGGED_IN

    search = FakeSearch({"kw1": [["b1", "b2", "b3"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search, like_fn)
    summary = await runner.run()
    history.close()

    assert summary.stop_reason == "not_logged_in"
    assert calls["n"] == 1                # 한 번만 시도하고 즉시 중단


# ---- MINOR: visits.outcome must distinguish liked/already/no_button/error ----

def test_classify_visit_outcome_liked_when_any_success():
    assert classify_visit_outcome(1, [LikeOutcome.SUCCESS, LikeOutcome.ERROR]) == "liked"


def test_classify_visit_outcome_already_when_no_success_but_already_liked():
    assert classify_visit_outcome(
        0, [LikeOutcome.ALREADY_LIKED, LikeOutcome.NO_BUTTON]
    ) == "already"


def test_classify_visit_outcome_no_button_when_all_attempts_had_no_button():
    assert classify_visit_outcome(
        0, [LikeOutcome.NO_BUTTON, LikeOutcome.NO_BUTTON]
    ) == "no_button"


def test_classify_visit_outcome_error_for_anything_else():
    assert classify_visit_outcome(0, [LikeOutcome.ERROR, LikeOutcome.TIMEOUT]) == "error"
    assert classify_visit_outcome(0, [LikeOutcome.NO_BUTTON, LikeOutcome.ERROR]) == "error"


async def test_visit_records_already_outcome_in_history(tmp_path):
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.ALREADY_LIKED))
    await runner.run()

    row = history.connection.execute(
        "SELECT outcome FROM visits WHERE blog_id=?", ("b1",)
    ).fetchone()
    history.close()
    assert row[0] == "already"


async def test_visit_records_no_button_outcome_in_history(tmp_path):
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.NO_BUTTON))
    await runner.run()

    row = history.connection.execute(
        "SELECT outcome FROM visits WHERE blog_id=?", ("b1",)
    ).fetchone()
    history.close()
    assert row[0] == "no_button"


# ---- MINOR: SearchPage.dropped must actually be surfaced ----

async def test_page_collected_carries_the_dropped_count(tmp_path):
    class PartiallyDroppedSearch:
        async def iter_pages(self, query, start_date, end_date, first_page=1):
            items = [SearchItem(blog_id="b1", log_no="100", title="t",
                                blog_name="b", add_date_ms=0)]
            yield 1, SearchPage(items=items, total_count=99, per_page=7, raw_count=3)

    events = []
    runner, history = _runner(tmp_path, _config(tmp_path), PartiallyDroppedSearch(),
                              await _always(LikeOutcome.SUCCESS), events)
    await runner.run()
    history.close()

    from engine.events import PageCollected, LogLine

    collected = [e for e in events if isinstance(e, PageCollected)]
    assert collected[0].dropped == 2   # raw_count(3) - len(items)(1)

    dropped_logs = [e for e in events if isinstance(e, LogLine) and "형식 오류" in e.text]
    assert len(dropped_logs) == 1


async def test_page_collected_does_not_log_when_nothing_was_dropped(tmp_path):
    from engine.events import LogLine

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.SUCCESS), events)
    await runner.run()
    history.close()

    assert not [e for e in events if isinstance(e, LogLine) and "형식 오류" in e.text]


# ---- I6: TIMEOUT is retried exactly once, per spec 7.1 ----

async def test_timeout_is_retried_once_and_recovers(tmp_path):
    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeOutcome.TIMEOUT if calls["n"] == 1 else LikeOutcome.SUCCESS

    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search, like_fn)
    summary = await runner.run()
    history.close()

    assert calls["n"] == 2            # 원 시도 1회 + 재시도 1회
    assert summary.likes_ok == 1      # 회복된 결과가 집계된다
    assert summary.likes_tried == 1   # 논리적으로는 여전히 이 글 1건 시도


async def test_timeout_recovery_emits_and_records_only_the_final_outcome(tmp_path):
    """재시도로 회복된 글은 차단 감지기 · UI 로그 모두에서 실패로 잡히면
    안 된다 — 잠깐 느렸던 글 하나가 연속 실패 카운터를 갉아먹지 않는다."""
    from engine.events import LikeResultEvent

    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeOutcome.TIMEOUT if calls["n"] == 1 else LikeOutcome.SUCCESS

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search,
                              like_fn, events)
    await runner.run()
    history.close()

    like_events = [e for e in events if isinstance(e, LikeResultEvent)]
    assert len(like_events) == 1
    assert like_events[0].outcome == "success"


async def test_timeout_retried_and_still_failing_skips_just_that_post(tmp_path):
    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeOutcome.TIMEOUT

    search = FakeSearch({"kw1": [["b1", "b2"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search, like_fn)
    summary = await runner.run()
    history.close()

    assert calls["n"] == 4            # 블로그 2개 * (원 시도 + 재시도)
    assert summary.likes_ok == 0
    assert summary.likes_tried == 2   # 블로그당 1건 시도로 집계 — 재시도는 안 겹친다
    assert summary.blogs_done == 2    # 계속 다음 블로그로 넘어간다
    assert summary.stop_reason == "exhausted"


# ---- I3: RunSummary.dry_run must reflect the config, for the runs table + UI ----

async def test_run_summary_carries_the_dry_run_flag(tmp_path):
    search = FakeSearch({"kw1": [["blog_a"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, dry_run=True), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.dry_run is True


async def test_run_summary_dry_run_flag_is_false_for_a_real_run(tmp_path):
    search = FakeSearch({"kw1": [["blog_a"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, dry_run=False), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.dry_run is False


# ---- Controller ruling 2 (R10): dry run must not write visit history ----

async def test_dry_run_does_not_write_visit_history(tmp_path):
    search = FakeSearch({"kw1": [["blog_a", "blog_b"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, dry_run=True), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()

    assert summary.blogs_done == 2
    assert summary.likes_ok == 6
    assert history.was_visited("acct_a", "blog_a") is False
    assert history.was_visited("acct_a", "blog_b") is False
    history.close()


# ---- Controller ruling 3 (R13): unexpected exception must not lose the tally ----

async def test_unexpected_exception_still_reports_tally_and_reraises(tmp_path):
    from engine.events import RunFinished

    events = []
    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("boom")
        return LikeOutcome.SUCCESS

    search = FakeSearch({"kw1": [["b1", "b2", "b3", "b4", "b5"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search, like_fn, events)
    db_path = tmp_path / "h.db"

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run()
    history.close()

    finished = [e for e in events if isinstance(e, RunFinished)]
    assert len(finished) == 1             # RunFinished가 정확히 한 번 방출된다
    assert finished[0].summary.likes_ok == 3
    assert finished[0].summary.stop_reason == "error"

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT finished_at, stop_reason FROM runs WHERE run_id=?", ("run1",)
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] == "error"


# ---- Fix 1: run() cancelled from outside must not lose the tally ----

async def test_run_cancelled_externally_still_reports_tally(tmp_path):
    from engine.events import RunFinished

    events = []
    calls = {"n": 0}
    ready = asyncio.Event()
    blocker = asyncio.Event()

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        if calls["n"] == 4:
            # b1은 이미 3개를 다 채웠다. b2의 첫 좋아요에서 멈춰 세우고
            # 테스트가 이 시점에 확실히 cancel()을 걸 수 있게 한다.
            ready.set()
            await blocker.wait()   # 절대 set되지 않는다 — cancel()로만 풀린다
        return LikeOutcome.SUCCESS

    search = FakeSearch({"kw1": [["b1", "b2", "b3", "b4", "b5"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search, like_fn, events)
    db_path = tmp_path / "h.db"

    task = asyncio.create_task(runner.run())
    await ready.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    history.close()

    finished = [e for e in events if isinstance(e, RunFinished)]
    assert len(finished) == 1             # RunFinished가 정확히 한 번 방출된다
    assert finished[0].summary.likes_ok == 3     # b1만 완료된 상태에서 잘렸다
    assert finished[0].summary.stop_reason == "user"

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE run_id=?", ("run1",)
    ).fetchone()
    conn.close()
    assert row[0] is not None             # runs 행이 열린 채로 남지 않는다


# ---- Fix 3: total search failure must not be reported as normal completion ----

# ---- I2: RSS 폴백은 사건으로 취급되어야 한다 ----

async def test_empty_rss_result_emits_fallback_used_naming_the_blog(tmp_path):
    from engine.events import FallbackUsed

    class EmptyPosts:
        async def recent_log_nos(self, blog_id, limit):
            return []

    events = []
    search = FakeSearch({"kw1": [["blog_a"]]})
    history = History(tmp_path / "h.db")
    runner = Runner(
        config=_config(tmp_path),
        history=history,
        search=search,
        posts=EmptyPosts(),
        like_fn=await _always(LikeOutcome.SUCCESS),
        limiter=_no_wait_limiter(),
        detector=BlockDetector(),
        emit=events.append,
        run_id="run1",
    )
    await runner.run()
    history.close()

    fallbacks = [e for e in events if isinstance(e, FallbackUsed)]
    assert len(fallbacks) == 1
    assert fallbacks[0].where == "posts"
    assert "blog_a" in fallbacks[0].reason


async def test_search_failure_for_every_keyword_is_reported_as_error(tmp_path):
    class FailingSearch:
        async def iter_pages(self, query, start_date, end_date, first_page=1):
            raise RuntimeError("network down")
            yield  # pragma: no cover - unreachable, keeps this an async generator

    search = FailingSearch()
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.SUCCESS))
    summary = await runner.run()
    history.close()

    assert summary.stop_reason == "error"
    assert summary.blogs_done == 0


# ---- 중단 사유는 어느 키워드에서 났는지 함께 알린다 ----
# 화면이 4개 패널 전부에 같은 빨간 배너를 띄우면, 참여하지 않은 패널까지
# 중단으로 보이고 정작 원인이 된 키워드는 어디에도 남지 않는다.


async def test_abort_names_the_keyword_it_came_from(tmp_path):
    from engine.events import Aborted

    events = []
    search = FakeSearch({"kw2": [["b1"]]})
    config = _config(tmp_path, keywords=["kw1", "kw2"])
    runner, history = _runner(tmp_path, config, search,
                              await _always(LikeOutcome.BLOCKED), events)
    await runner.run()
    history.close()

    aborts = [e for e in events if isinstance(e, Aborted)]
    assert [a.keyword for a in aborts] == ["kw2"]


async def test_session_loss_abort_also_names_the_keyword(tmp_path):
    from engine.events import Aborted

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path), search,
                              await _always(LikeOutcome.NOT_LOGGED_IN), events)
    await runner.run()
    history.close()

    assert [e.keyword for e in events if isinstance(e, Aborted)] == ["kw1"]


async def test_search_failure_abort_keeps_its_keyword(tmp_path):
    from engine.events import Aborted

    class BoomSearch:
        async def iter_pages(self, query, start_date, end_date, first_page=1):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    events = []
    runner, history = _runner(tmp_path, _config(tmp_path), BoomSearch(),
                              await _always(LikeOutcome.SUCCESS), events)
    await runner.run()
    history.close()

    assert [e.keyword for e in events if isinstance(e, Aborted)] == ["kw1"]


# ---- 타임아웃 단계는 이벤트를 타고 화면 · 실행 로그까지 간다 ----

async def test_like_result_event_carries_the_detail(tmp_path):
    from engine.events import LikeResultEvent
    from engine.models import LikeResult

    async def like_fn(blog_id, log_no):
        return LikeResult(LikeOutcome.TIMEOUT, "클릭 후 on 확인")

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search,
                              like_fn, events)
    await runner.run()
    history.close()

    like_events = [e for e in events if isinstance(e, LikeResultEvent)]
    assert [e.detail for e in like_events] == ["클릭 후 on 확인"]


async def test_plain_outcomes_still_work_and_carry_no_detail(tmp_path):
    """like_fn이 LikeOutcome만 돌려줘도 러너는 그대로 동작한다."""
    from engine.events import LikeResultEvent

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search,
                              await _always(LikeOutcome.SUCCESS), events)
    summary = await runner.run()
    history.close()

    assert summary.likes_ok == 1
    assert [e.detail for e in events if isinstance(e, LikeResultEvent)] == [""]


async def test_retry_reports_the_detail_of_the_final_attempt(tmp_path):
    from engine.events import LikeResultEvent
    from engine.models import LikeResult

    calls = {"n": 0}

    async def like_fn(blog_id, log_no):
        calls["n"] += 1
        return LikeResult(LikeOutcome.TIMEOUT,
                          "페이지 로딩" if calls["n"] == 1 else "클릭 후 on 확인")

    events = []
    search = FakeSearch({"kw1": [["b1"]]})
    runner, history = _runner(tmp_path, _config(tmp_path, likes_per_blog=1), search,
                              like_fn, events)
    await runner.run()
    history.close()

    assert [e.detail for e in events if isinstance(e, LikeResultEvent)] == ["클릭 후 on 확인"]
