"""엔진 전반에서 오가는 값 객체."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

TOTAL_COUNT_CAP = 1000     # 네이버가 거는 상한 (실측 2026-08-30)


@dataclass(frozen=True)
class SearchItem:
    blog_id: str
    log_no: str
    title: str
    blog_name: str
    add_date_ms: int


@dataclass(frozen=True)
class SearchPage:
    items: list[SearchItem]
    total_count: int
    per_page: int
    raw_count: int  # Number of rows in searchList before filtering

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def is_capped(self) -> bool:
        """totalCount가 상한에 걸렸는가. 진행률을 '1000+'로 표기해야 한다."""
        return self.total_count >= TOTAL_COUNT_CAP

    @property
    def dropped(self) -> int:
        """Number of searchList rows dropped due to missing required fields."""
        return self.raw_count - len(self.items)


@dataclass(frozen=True)
class Target:
    """소비자가 처리할 블로그 하나."""
    blog_id: str
    keyword: str
    seed_log_no: str    # RSS 실패 시 폴백으로 쓸 글 번호


class LikeOutcome(Enum):
    SUCCESS = "success"
    ALREADY_LIKED = "already_liked"
    NO_BUTTON = "no_button"
    NOT_LOGGED_IN = "not_logged_in"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    ERROR = "error"
