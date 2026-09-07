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


@dataclass(frozen=True)
class LikeResult:
    """공감 시도 하나의 결과.

    `detail`은 진단 전용이다. `TIMEOUT` 하나가 페이지 로딩 · 버튼 탐색 ·
    스크롤 · 클릭 · 클릭 후 확인 다섯 군데에서 똑같이 나오면, 5건 연속
    실패로 실행이 멈췄을 때 무엇을 고쳐야 하는지 알 방법이 없다 (레거시
    결함 9 — "공감 없음 or 이미 함"). 어느 단계였는지를 값에 실어 화면과
    실행 로그(JSONL)까지 그대로 흘려보낸다.

    보안 규칙: 여기에 예외 메시지 본문을 담지 않는다. 예외에 storage_state가
    실린 적이 있고, 이 값은 파일로 기록된다 — `engine.like.stage_detail` 참고.
    """
    outcome: LikeOutcome
    detail: str = ""
