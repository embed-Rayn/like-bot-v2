"""엔진이 방출하는 진행 이벤트. UI와의 유일한 접점이다.

엔진은 이 값을 뱉을 뿐 누가 받는지 모른다. 데스크톱은 Qt 시그널로, 나중에
웹은 같은 이벤트를 웹소켓으로 중계한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkerStarted:
    keyword: str


@dataclass(frozen=True)
class PageCollected:
    keyword: str
    page: int
    found: int          # 응답에 들어 있던 건수
    queued: int         # 중복 제거 후 큐에 넣은 건수
    total_count: int    # 검색 API의 totalCount (1000이면 상한)


@dataclass(frozen=True)
class BlogVisited:
    keyword: str
    blog_id: str
    likes_ok: int
    likes_tried: int


@dataclass(frozen=True)
class LikeResultEvent:
    keyword: str         # MINOR: 어느 패널로 보낼지 판단하는 데 필요하다
    blog_id: str
    log_no: str
    outcome: str        # LikeOutcome.value


@dataclass(frozen=True)
class FallbackUsed:
    where: str          # "search" | "posts"
    reason: str


@dataclass(frozen=True)
class Aborted:
    reason: str


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    blogs_done: int = 0
    likes_ok: int = 0
    likes_tried: int = 0
    stop_reason: str = "unknown"   # budget | exhausted | user | blocked | not_logged_in | error
    per_keyword: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False   # I3: 드라이런 결과를 실제 실행과 구분해 표시하기 위함


@dataclass(frozen=True)
class RunFinished:
    summary: RunSummary


@dataclass(frozen=True)
class LogLine:
    keyword: str        # 전역 메시지는 빈 문자열
    text: str


Event = (
    WorkerStarted | PageCollected | BlogVisited | LikeResultEvent
    | FallbackUsed | Aborted | RunFinished | LogLine
)
