"""실행 설정과 그 검증.

기본값은 DEFAULTS 한 곳에만 있다. UI와 엔진이 같은 값을 본다.
검증은 필드마다 독립적으로 이루어져, 한 필드가 틀려도 나머지가 조용히
초기화되지 않는다 (레거시 결함 8).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DEFAULTS: dict[str, object] = {
    "keywords": [],
    "excludes": [],
    "blog_limit": 200,
    "likes_per_blog": 3,
    "likes_per_minute": 6.0,   # 보수적 시작값 — 운영하며 조정한다
    "dry_run": False,
}


def default_dates() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=1)).isoformat(), today.isoformat()


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


def _as_int(raw, field: str, *, minimum: int, errors: list[FieldError]) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        errors.append(FieldError(field, "정수를 입력하세요."))
        return None
    if value < minimum:
        errors.append(FieldError(field, f"{minimum} 이상이어야 합니다."))
        return None
    return value


def _as_float(raw, field: str, *, minimum: float, errors: list[FieldError]) -> float | None:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        errors.append(FieldError(field, "숫자를 입력하세요."))
        return None
    if value < minimum:
        errors.append(FieldError(field, f"{minimum} 이상이어야 합니다."))
        return None
    return value


def _as_date(raw, field: str, errors: list[FieldError]) -> date | None:
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        errors.append(FieldError(field, "YYYY-MM-DD 형식이어야 합니다."))
        return None


@dataclass(frozen=True)
class RunConfig:
    account: str
    keywords: list[str]
    excludes: list[str]
    start_date: str
    end_date: str
    blog_limit: int
    likes_per_blog: int
    likes_per_minute: float
    dry_run: bool

    @classmethod
    def validate(cls, raw: dict) -> tuple["RunConfig | None", list[FieldError]]:
        errors: list[FieldError] = []

        account = str(raw.get("account", "")).strip()
        if not account:
            errors.append(FieldError("account", "네이버 아이디를 입력하세요."))

        keywords = [k.strip() for k in (raw.get("keywords") or []) if str(k).strip()]
        if not keywords:
            errors.append(FieldError("keywords", "키워드를 하나 이상 입력하세요."))

        excludes = [e.strip() for e in (raw.get("excludes") or []) if str(e).strip()]

        start = _as_date(raw.get("start_date"), "start_date", errors)
        end = _as_date(raw.get("end_date"), "end_date", errors)
        if start and end and end < start:
            errors.append(FieldError("end_date", "종료일이 시작일보다 빠릅니다."))

        blog_limit = _as_int(raw.get("blog_limit"), "blog_limit", minimum=1, errors=errors)
        likes_per_blog = _as_int(raw.get("likes_per_blog"), "likes_per_blog",
                                 minimum=1, errors=errors)
        likes_per_minute = _as_float(raw.get("likes_per_minute"), "likes_per_minute",
                                     minimum=0.1, errors=errors)

        if errors:
            return None, errors

        return cls(
            account=account,
            keywords=keywords,
            excludes=excludes,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            blog_limit=blog_limit,
            likes_per_blog=likes_per_blog,
            likes_per_minute=likes_per_minute,
            dry_run=bool(raw.get("dry_run", DEFAULTS["dry_run"])),
        ), []

    def search_query(self, keyword: str) -> str:
        """네이버 검색식. 제외 단어는 ' -단어'로 붙는다."""
        parts = [keyword.strip()]
        parts += [f"-{word}" for word in self.excludes]
        return " ".join(parts)
