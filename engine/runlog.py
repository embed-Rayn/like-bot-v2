"""실행 하나를 파일 하나에 한 줄씩 남긴다 (JSON Lines).

이벤트는 이미 구조화되어 있으므로(`engine/events.py`) 화면에 뿌리는 것과 같은
값을 그대로 적으면 된다. 사후 진단이 목적이다 — 배포본은 console=False라
운영자가 창을 닫고 나면 무슨 일이 있었는지 물어볼 데가 없다.

엔진은 이 모듈을 모른다. `emit` 콜백을 감싸는 것은 호출부(desktop)의 일이고,
그래야 "엔진은 뱉을 뿐 누가 받는지 모른다"는 성질이 유지된다.

주의 — `crash.log`와 같은 위험: `LogLine.text`에는 예외 메시지가 실릴 수 있다.
세션(storage_state)이 예외에 실리는 버그가 재발하면 이 파일에 그대로 남는다.
그런 일이 생기면 보안 규칙대로 파일을 지우고 네이버에서 세션을 폐기할 것.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from engine.config import RunConfig
from engine.events import Event
from engine.paths import AppPaths

# 남겨 둘 실행 기록 개수. 진단은 최근 것만 쓰이고, 오래된 것은 디스크만 먹는다.
KEEP_RUNS = 30

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class RunLog:
    """이벤트를 받아 파일에 적는다. 실패하면 조용히 포기한다.

    기록이 실행을 죽이면 안 된다 — 로그를 남기려다 공감을 못 누르는 것은
    본말전도다. 그래서 쓰기 실패는 예외로 올라가지 않고 `failed`만 세운다.
    """

    def __init__(self, handle: IO[str] | None, path: Path | None) -> None:
        self._handle = handle
        self.path = path
        self.failed = handle is None

    @classmethod
    def disabled(cls) -> "RunLog":
        """파일을 열지 못했을 때의 자리 표시. 호출부는 분기하지 않아도 된다."""
        return cls(None, None)

    def write(self, event: Event) -> None:
        fields = asdict(event) if is_dataclass(event) else {"repr": repr(event)}
        self._line({"t": _now(), "type": type(event).__name__, **fields})

    def close(self) -> None:
        self._shut()

    def _line(self, record: dict) -> None:
        if self._handle is None or self.failed:
            return
        try:
            # 매 줄 flush — 실행이 중간에 죽어도 거기까지는 남아 있어야 한다.
            # 그 순간이 바로 로그가 필요한 순간이다.
            self._handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._handle.flush()
        except Exception:
            self.failed = True
            self._shut()

    def _shut(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_run_log(
    paths: AppPaths,
    run_id: str,
    config: RunConfig,
    *,
    keep: int = KEEP_RUNS,
) -> RunLog:
    """`logs/run-{시각}-{run_id}.jsonl`을 열고 헤더 한 줄을 적는다."""
    try:
        paths.ensure()
        safe = _UNSAFE.sub("_", run_id.strip()) or "unknown"
        target = paths.log_dir / f"run-{datetime.now():%Y%m%d-%H%M%S}-{safe}.jsonl"
        handle = target.open("w", encoding="utf-8")
    except Exception:
        return RunLog.disabled()

    log = RunLog(handle, target)
    # 헤더는 config에서 필드를 하나씩 골라 담는다. asdict로 통째로 담으면
    # 나중에 config에 붙는 민감한 필드가 아무도 모르게 파일로 새어 나간다.
    log._line({
        "t": _now(),
        "type": "RunStarted",
        "run_id": run_id,
        "account": config.account,
        "keywords": list(config.keywords),
        "excludes": list(config.excludes),
        "start_date": config.start_date,
        "end_date": config.end_date,
        "blog_limit": config.blog_limit,
        "likes_per_blog": config.likes_per_blog,
        "likes_per_minute": config.likes_per_minute,
        "dry_run": config.dry_run,
    })
    # 새 파일을 만든 뒤에 정리한다 — 이름이 시각순이라 방금 연 파일이 항상
    # 가장 새것이고, 자기 자신을 지울 일이 없다.
    prune(paths.log_dir, keep=keep)
    return log


def prune(log_dir: Path, *, keep: int = KEEP_RUNS) -> None:
    """오래된 `run-*.jsonl`만 지운다. `crash.log`는 건드리지 않는다."""
    try:
        runs = sorted(log_dir.glob("run-*.jsonl"))
    except Exception:
        return
    for stale in runs[: max(0, len(runs) - keep)]:
        try:
            stale.unlink()
        except OSError:
            pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
