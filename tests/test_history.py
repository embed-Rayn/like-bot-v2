import sqlite3

import pytest

from engine.history import History


@pytest.fixture
def history(tmp_path):
    h = History(tmp_path / "history.db")
    yield h
    h.close()


def test_unvisited_blog_returns_false(history):
    assert history.was_visited("acct_a", "blog_1") is False


def test_recorded_visit_is_remembered(history):
    history.record_visit("acct_a", "blog_1", "run1", "헬스장", 3, 3, "liked")
    assert history.was_visited("acct_a", "blog_1") is True


def test_history_is_scoped_per_account(history):
    """계정 B는 계정 A가 다녀온 블로그에 아직 간 적이 없다."""
    history.record_visit("acct_a", "blog_1", "run1", "헬스장", 3, 3, "liked")
    assert history.was_visited("acct_b", "blog_1") is False


def test_same_blog_can_be_recorded_under_two_accounts(history):
    history.record_visit("acct_a", "blog_1", "run1", "헬스장", 3, 3, "liked")
    history.record_visit("acct_b", "blog_1", "run2", "헬스장", 2, 3, "liked")
    assert history.visited_count("acct_a") == 1
    assert history.visited_count("acct_b") == 1


def test_re_recording_same_pair_does_not_duplicate(history):
    history.record_visit("acct_a", "blog_1", "run1", "헬스장", 3, 3, "liked")
    history.record_visit("acct_a", "blog_1", "run2", "헬스장", 1, 3, "liked")
    assert history.visited_count("acct_a") == 1


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "history.db"
    first = History(path)
    first.record_visit("acct_a", "blog_1", "run1", "헬스장", 3, 3, "liked")
    first.close()

    second = History(path)
    assert second.was_visited("acct_a", "blog_1") is True
    second.close()


def test_run_lifecycle_is_recorded(history):
    history.start_run("run1", "acct_a", ["헬스장", "필라테스"])
    history.finish_run("run1", blogs_done=12, likes_ok=30, stop_reason="budget")
    row = history.connection.execute(
        "SELECT blogs_done, likes_ok, stop_reason, finished_at FROM runs WHERE run_id=?",
        ("run1",),
    ).fetchone()
    assert row[0] == 12 and row[1] == 30 and row[2] == "budget"
    assert row[3] is not None


def test_wal_mode_is_enabled(history):
    mode = history.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


# ---- I3: 드라이런 결과는 runs 테이블에서 실제 실행과 구분되어야 한다 ----


def test_start_run_defaults_dry_run_to_false(history):
    history.start_run("run1", "acct_a", ["헬스장"])
    row = history.connection.execute(
        "SELECT dry_run FROM runs WHERE run_id=?", ("run1",)
    ).fetchone()
    assert row[0] == 0


def test_start_run_records_dry_run_flag(history):
    history.start_run("run1", "acct_a", ["헬스장"], dry_run=True)
    row = history.connection.execute(
        "SELECT dry_run FROM runs WHERE run_id=?", ("run1",)
    ).fetchone()
    assert row[0] == 1


def test_opening_a_pre_existing_db_without_the_dry_run_column_does_not_crash(tmp_path):
    """이 브랜치 개발 중 만들어졌을 수 있는, dry_run 컬럼이 없는 옛 history.db를
    열어도 죽지 않고 컬럼이 추가되어야 한다."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE visits (
            account     TEXT NOT NULL,
            blog_id     TEXT NOT NULL,
            run_id      TEXT NOT NULL,
            keyword     TEXT NOT NULL,
            visited_at  TEXT NOT NULL,
            likes_ok    INTEGER NOT NULL,
            likes_tried INTEGER NOT NULL,
            outcome     TEXT NOT NULL,
            PRIMARY KEY (account, blog_id)
        );
        CREATE TABLE runs (
            run_id       TEXT PRIMARY KEY,
            started_at   TEXT NOT NULL,
            finished_at  TEXT,
            account      TEXT NOT NULL,
            keywords     TEXT NOT NULL,
            blogs_done   INTEGER NOT NULL DEFAULT 0,
            likes_ok     INTEGER NOT NULL DEFAULT 0,
            stop_reason  TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    reopened = History(path)          # 마이그레이션이 여기서 죽지 않아야 한다
    reopened.start_run("run1", "acct_a", ["헬스장"], dry_run=True)
    row = reopened.connection.execute(
        "SELECT dry_run FROM runs WHERE run_id=?", ("run1",)
    ).fetchone()
    assert row[0] == 1
    reopened.close()
