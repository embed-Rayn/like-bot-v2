"""방문 기록과 실행 이력.

기록의 범위는 로그인 계정이다. (account, blog_id) 복합 기본키를 쓰지 않으면
계정 B가 계정 A의 방문 이력 때문에 블로그를 건너뛴다 — 계정 B는 그곳에 간 적이
없는데도. 스펙 §6.4.

쓰기는 소비자 코루틴 한 곳에서만, 블로그 1개당 1회 일어나므로 동기 호출로
충분하다. WAL 모드는 나중에 웹 UI가 실행 중인 DB를 읽기 전용으로 붙기 위한 것이다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
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

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    account      TEXT NOT NULL,
    keywords     TEXT NOT NULL,
    blogs_done   INTEGER NOT NULL DEFAULT 0,
    likes_ok     INTEGER NOT NULL DEFAULT 0,
    stop_reason  TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class History:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self._migrate_add_dry_run_column()
        self.connection.commit()

    def _migrate_add_dry_run_column(self) -> None:
        """I3: dry_run을 구분하기 위한 컬럼.

        `CREATE TABLE IF NOT EXISTS`는 이미 존재하는 테이블에는 새 컬럼을
        추가하지 않는다. 이 프로젝트는 아직 배포된 DB가 없으므로 스키마에
        컬럼을 추가하는 것만으로 충분하다고 볼 수도 있지만, 이 브랜치를
        개발하며 이미 만들어 둔 (dry_run 컬럼이 없는) history.db를 여는
        경우에도 죽지 않도록 필요하면 직접 추가한다.
        """
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "dry_run" not in columns:
            self.connection.execute(
                "ALTER TABLE runs ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        self.connection.close()

    def was_visited(self, account: str, blog_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM visits WHERE account=? AND blog_id=? LIMIT 1",
            (account, blog_id),
        ).fetchone()
        return row is not None

    def visited_count(self, account: str) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM visits WHERE account=?", (account,)
        ).fetchone()[0]

    def record_visit(
        self,
        account: str,
        blog_id: str,
        run_id: str,
        keyword: str,
        likes_ok: int,
        likes_tried: int,
        outcome: str,
    ) -> None:
        self.connection.execute(
            """INSERT INTO visits
                 (account, blog_id, run_id, keyword, visited_at, likes_ok,
                  likes_tried, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account, blog_id) DO NOTHING""",
            (account, blog_id, run_id, keyword, _now(), likes_ok, likes_tried, outcome),
        )
        self.connection.commit()

    def start_run(
        self,
        run_id: str,
        account: str,
        keywords: list[str],
        *,
        dry_run: bool = False,
    ) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO runs
                 (run_id, started_at, account, keywords, blogs_done, likes_ok, dry_run)
               VALUES (?, ?, ?, ?, 0, 0, ?)""",
            (run_id, _now(), account, json.dumps(keywords, ensure_ascii=False),
             int(dry_run)),
        )
        self.connection.commit()

    def finish_run(
        self, run_id: str, blogs_done: int, likes_ok: int, stop_reason: str
    ) -> None:
        self.connection.execute(
            """UPDATE runs
                  SET finished_at=?, blogs_done=?, likes_ok=?, stop_reason=?
                WHERE run_id=?""",
            (_now(), blogs_done, likes_ok, stop_reason, run_id),
        )
        self.connection.commit()
