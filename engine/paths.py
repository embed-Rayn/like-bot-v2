"""저장 위치를 여기 한 곳에서만 결정한다.

레거시는 open("accounts.csv")처럼 작업 디렉터리 상대 경로를 써서, exe를 다른
위치에서 실행하면 파일을 찾지 못했다. 다른 모듈은 경로를 만들지 않고 주입받는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "like-bot-v2"
    return Path.home() / ".like-bot-v2"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def for_app(cls, root: Path | None = None) -> "AppPaths":
        return cls(Path(root) if root is not None else _default_root())

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def history_db(self) -> Path:
        return self.root / "history.db"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def browsers_dir(self) -> Path:
        """배포본이 chromium을 내려받는 곳. 만들지 않는다 — playwright가 만든다."""
        return self.root / "browsers"

    def session_file(self, account: str) -> Path:
        safe = _UNSAFE.sub("_", account.strip()) or "unknown"
        return self.sessions_dir / f"{safe}.dat"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
