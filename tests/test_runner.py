import asyncio
import sqlite3

import pytest

from engine.config import RunConfig
from engine.history import History
from engine.models import LikeOutcome, SearchItem, SearchPage
from engine.ratelimit import RateLimiter
from engine.runner import Runner
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
