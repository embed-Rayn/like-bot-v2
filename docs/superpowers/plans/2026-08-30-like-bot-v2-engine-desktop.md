# like-bot-v2 엔진 + 데스크톱 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네이버 블로그 검색 결과를 순회하며 운영자 계정으로 블로그당 N개의 공감을 누르는 엔진과, 그것을 눈으로 보며 운용하는 PyQt6 데스크톱 앱을 만든다.

**Architecture:** 대상 발굴(검색 API + RSS)은 비로그인 HTTP로, 공감은 로그인된 Playwright 탭 하나로 처리한다. 키워드마다 생산자 코루틴이 대상을 큐에 넣고, 소비자 코루틴 하나가 계정 단위 속도 제한을 지키며 공감한다. 엔진은 순수 asyncio이며 UI를 전혀 모르고, 진행 상황을 이벤트로만 방출한다.

**Tech Stack:** Python 3.13 · asyncio · httpx · Playwright · PyQt6 · SQLite(stdlib) · keyring · pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-08-30-like-bot-v2-design.md`

## Global Constraints

이 절의 값은 스펙에서 그대로 옮긴 것이다. 모든 태스크의 요구사항에 암묵적으로 포함된다.

- **Python 3.13** (개발 환경 실측: 3.13.14)
- 검색 API: `https://section.blog.naver.com/ajax/SearchList.naver`, **`Referer: https://section.blog.naver.com/Search/Post.naver` 헤더 필수**, 응답은 `)]}',` 접두사 뒤 JSON
- `countPerPage=7` 고정. `totalCount`는 **1000이 상한**, 143페이지가 마지막(144페이지부터 0건)
- RSS: `https://rss.blog.naver.com/{blogId}.xml`, `channel/item/link`가 **CDATA로 감싸짐**
- 방문 기록 기본키는 **`(account, blog_id)`** — 계정별로 분리
- 세션(`storage_state`)은 **계정별 파일**로 저장
- 브라우저 **탭은 1개** — 공감은 단일 스트림
- 속도 제한 기본값은 **보수적으로(분당 한 자릿수)**
- **비밀번호와 세션 쿠키는 어떤 로그에도 남기지 않는다**
- 블로그당 공감 수 기본값 **3**
- BFS/공감자 수집/`Depth`/`except bot`은 **구현하지 않는다** (결정 1)

---

## File Structure

```
like-bot-v2/
├── pyproject.toml
├── engine/
│   ├── __init__.py
│   ├── paths.py          저장 위치 결정 — 다른 모듈은 주입받는다
│   ├── events.py         진행 이벤트 dataclass (UI와의 유일한 접점)
│   ├── config.py         RunConfig — 필드별 검증
│   ├── models.py         SearchItem · SearchPage · Target · LikeOutcome
│   ├── search.py         검색 파서(순수) + 페이지네이션(HTTP)
│   ├── posts.py          RSS 파서(순수) + 조회(HTTP)
│   ├── history.py        SQLite — visits · runs
│   ├── ratelimit.py      간격 페이싱 + 지터
│   ├── safety.py         차단 감지 (연속 실패 · 성공률)
│   ├── session.py        Playwright 기동 · 로그인 · storage_state
│   ├── like.py           공감 클릭 — 결과를 유형으로 반환
│   └── runner.py         생산자/소비자 오케스트레이션 · 종료 처리
├── desktop/
│   ├── __init__.py
│   ├── bridge.py         asyncio 루프 ↔ Qt 시그널
│   ├── widgets.py        키워드 패널 위젯
│   └── app.py            메인 창 · 진입점
├── tools/
│   └── refresh_fixtures.py   실측 응답을 tests/fixtures/에 갱신
└── tests/
    ├── fixtures/
    │   ├── search_page1.json
    │   ├── search_empty.json
    │   └── rss_sample.xml
    ├── test_config.py · test_search_parse.py · test_posts_parse.py
    ├── test_history.py · test_ratelimit.py · test_safety.py
    ├── test_runner.py
    └── test_contract.py      층 2 — 네트워크 O, 로그인 X
```

**분리 원칙:** 순수 파서(파일 상단, 네트워크 없음)와 HTTP 호출(하단)을 같은 모듈 안에서 함수로 나눈다. 파서는 픽스처로 테스트하고 HTTP는 계약 테스트에서만 건드린다.

---

## Task 1: 스캐폴딩 · paths · events

**Files:**
- Create: `pyproject.toml`, `engine/__init__.py`, `engine/paths.py`, `engine/events.py`
- Create: `tools/refresh_fixtures.py`, `tests/fixtures/` (3개 파일)
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `engine.paths.AppPaths` — `AppPaths.for_app(root: Path | None = None) -> AppPaths`, 속성 `config_file: Path`, `history_db: Path`, `sessions_dir: Path`, `log_dir: Path`, 메서드 `session_file(account: str) -> Path`, `ensure() -> None`
  - `engine.events` — dataclass `WorkerStarted(keyword)`, `PageCollected(keyword, page, found, queued, total_count)`, `BlogVisited(keyword, blog_id, likes_ok, likes_tried)`, `LikeResultEvent(blog_id, log_no, outcome)`, `FallbackUsed(where, reason)`, `Aborted(reason)`, `RunFinished(summary)`, `LogLine(keyword, text)`; 공용 타입 별칭 `Event`

- [ ] **Step 1: `pyproject.toml` 작성**

```toml
[project]
name = "like-bot-v2"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.27",
    "playwright>=1.47",
    "PyQt6>=6.7",
    "keyring>=25",
    "pywin32>=306; sys_platform == 'win32'",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["contract: 네이버에 실제 요청을 보내는 계약 테스트 (비로그인)"]
addopts = "-m 'not contract'"
```

- [ ] **Step 2: 의존성 설치**

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

- [ ] **Step 3: 실패하는 테스트 작성 — `tests/test_paths.py`**

```python
from pathlib import Path
from engine.paths import AppPaths


def test_session_file_is_per_account(tmp_path):
    p = AppPaths.for_app(tmp_path)
    a = p.session_file("account_a")
    b = p.session_file("account_b")
    assert a != b
    assert a.parent == p.sessions_dir


def test_session_filename_is_sanitized(tmp_path):
    p = AppPaths.for_app(tmp_path)
    f = p.session_file("we/ir:d id")
    assert "/" not in f.name and ":" not in f.name and " " not in f.name
    assert f.suffix == ".dat"


def test_ensure_creates_directories(tmp_path):
    p = AppPaths.for_app(tmp_path)
    p.ensure()
    assert p.sessions_dir.is_dir()
    assert p.log_dir.is_dir()


def test_all_paths_live_under_root(tmp_path):
    p = AppPaths.for_app(tmp_path)
    for path in (p.config_file, p.history_db, p.sessions_dir, p.log_dir):
        assert tmp_path in path.parents or path.parent == tmp_path
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `python -m pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.paths'`

- [ ] **Step 5: `engine/__init__.py` 생성 (빈 파일)**

```python
```

- [ ] **Step 6: `engine/paths.py` 구현**

```python
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

    def session_file(self, account: str) -> Path:
        safe = _UNSAFE.sub("_", account.strip()) or "unknown"
        return self.sessions_dir / f"{safe}.dat"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `python -m pytest tests/test_paths.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: `engine/events.py` 구현**

```python
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
    stop_reason: str = "unknown"   # budget | exhausted | user | blocked | error
    per_keyword: dict[str, int] = field(default_factory=dict)


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
```

- [ ] **Step 9: 픽스처 수집 스크립트 작성 — `tools/refresh_fixtures.py`**

```python
"""실측 응답을 tests/fixtures/에 저장한다. 손으로 지어낸 샘플보다 정확하다.

  python tools/refresh_fixtures.py
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://section.blog.naver.com/Search/Post.naver"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    return urllib.request.urlopen(req, timeout=20).read()


def _search(page: int) -> bytes:
    q = urllib.parse.urlencode({
        "countPerPage": 7, "currentPage": page, "keyword": "헬스장", "type": "post",
        "orderBy": "sim", "rangeType": "ALL", "startDate": "", "endDate": "",
    })
    return _get(f"https://section.blog.naver.com/ajax/SearchList.naver?{q}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    page1 = _search(1)
    (OUT / "search_page1.json").write_bytes(page1)

    # 144페이지는 0건 — 종료 판정 테스트용
    (OUT / "search_empty.json").write_bytes(_search(144))

    # page1의 첫 블로그로 RSS 픽스처를 만든다
    body = page1.decode("utf-8").split("\n", 1)[1]
    blog_id = json.loads(body)["result"]["searchList"][0]["domainIdOrBlogId"]
    (OUT / "rss_sample.xml").write_bytes(_get(f"https://rss.blog.naver.com/{blog_id}.xml"))

    print(f"저장 완료: {OUT}  (rss 대상 blog_id={blog_id})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: 픽스처 생성 및 확인**

Run:
```bash
python tools/refresh_fixtures.py
python -c "from pathlib import Path; [print(p.name, p.stat().st_size) for p in sorted(Path('tests/fixtures').iterdir())]"
```
Expected: `search_page1.json`(수만 바이트), `search_empty.json`(작음), `rss_sample.xml`(수만 바이트) 세 개가 출력된다.

- [ ] **Step 11: 커밋**

```bash
git add pyproject.toml engine/__init__.py engine/paths.py engine/events.py tools/refresh_fixtures.py tests/test_paths.py tests/fixtures/
git commit -m "feat: scaffold project with per-account paths and engine events"
```

---

## Task 2: config.py — 필드별 검증

레거시 결함 8을 여기서 끝낸다. `except ValueError` 하나가 8개 필드를 전부 되돌리고 그 폴백값이 UI 표시값과 달랐다.

**Files:**
- Create: `engine/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `engine.config.DEFAULTS: dict[str, object]` — 기본값의 **유일한 정의처**
  - `engine.config.FieldError(field: str, message: str)` — dataclass
  - `engine.config.RunConfig` — dataclass, 필드: `account: str`, `keywords: list[str]`, `excludes: list[str]`, `start_date: str`, `end_date: str`, `blog_limit: int`, `likes_per_blog: int`, `likes_per_minute: float`, `dry_run: bool`
  - `RunConfig.validate(raw: dict) -> tuple[RunConfig | None, list[FieldError]]` — classmethod
  - `RunConfig.search_query(keyword: str) -> str` — 제외 단어를 ` -단어`로 붙인 검색식

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_config.py`**

```python
from engine.config import DEFAULTS, RunConfig


def _raw(**over):
    base = {
        "account": "myblog",
        "keywords": ["헬스장"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": "200",
        "likes_per_blog": "3",
        "likes_per_minute": "6",
        "dry_run": False,
    }
    base.update(over)
    return base


def test_valid_config_parses():
    cfg, errors = RunConfig.validate(_raw())
    assert errors == []
    assert cfg.blog_limit == 200
    assert cfg.likes_per_blog == 3


def test_one_bad_field_does_not_reset_the_others():
    """레거시 결함 8: except ValueError 하나가 8개 필드를 전부 되돌렸다."""
    cfg, errors = RunConfig.validate(_raw(likes_per_blog="셋"))
    assert cfg is None
    assert [e.field for e in errors] == ["likes_per_blog"]


def test_multiple_bad_fields_are_all_reported():
    cfg, errors = RunConfig.validate(_raw(likes_per_blog="x", blog_limit="-5"))
    assert cfg is None
    assert {e.field for e in errors} == {"likes_per_blog", "blog_limit"}


def test_keywords_must_not_be_empty():
    cfg, errors = RunConfig.validate(_raw(keywords=[]))
    assert cfg is None
    assert [e.field for e in errors] == ["keywords"]


def test_end_date_must_not_precede_start_date():
    cfg, errors = RunConfig.validate(_raw(start_date="2026-08-30", end_date="2026-08-29"))
    assert cfg is None
    assert [e.field for e in errors] == ["end_date"]


def test_search_query_appends_exclusions():
    cfg, errors = RunConfig.validate(_raw(excludes=["협찬", "체험단", ""]))
    assert errors == []
    assert cfg.search_query("헬스장") == "헬스장 -협찬 -체험단"


def test_defaults_are_defined_once_and_are_conservative():
    assert DEFAULTS["likes_per_blog"] == 3
    assert DEFAULTS["likes_per_minute"] < 10   # 보수적 기본값
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.config'`

- [ ] **Step 3: `engine/config.py` 구현**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/config.py tests/test_config.py
git commit -m "feat: add RunConfig with per-field validation"
```

---

## Task 3: 검색 응답 파서 (순수 함수)

**Files:**
- Create: `engine/models.py`, `engine/search.py` (파서 부분만)
- Test: `tests/test_search_parse.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `engine.models.SearchItem` — dataclass: `blog_id: str`, `log_no: str`, `title: str`, `blog_name: str`, `add_date_ms: int`
  - `engine.models.SearchPage` — dataclass: `items: list[SearchItem]`, `total_count: int`, `per_page: int`; 속성 `is_empty: bool`, `is_capped: bool`
  - `engine.search.JSON_PREFIX: str`
  - `engine.search.parse_search_response(raw: str | bytes) -> SearchPage`
  - `engine.search.SearchParseError(Exception)`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_search_parse.py`**

```python
from pathlib import Path

import pytest

from engine.search import JSON_PREFIX, SearchParseError, parse_search_response

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_strips_xssi_prefix_and_parses():
    page = parse_search_response(_fixture("search_page1.json"))
    assert page.per_page == 7
    assert len(page.items) == 7


def test_extracts_blog_id_and_log_no_as_strings():
    page = parse_search_response(_fixture("search_page1.json"))
    first = page.items[0]
    assert first.blog_id and isinstance(first.blog_id, str)
    assert first.log_no.isdigit()          # int로 오지만 문자열로 정규화한다


def test_total_count_of_1000_is_flagged_as_capped():
    page = parse_search_response(_fixture("search_page1.json"))
    assert page.total_count == 1000
    assert page.is_capped is True


def test_empty_page_is_detected():
    page = parse_search_response(_fixture("search_empty.json"))
    assert page.items == []
    assert page.is_empty is True


def test_parses_without_the_prefix_too():
    raw = _fixture("search_page1.json").decode("utf-8")
    body = raw.split("\n", 1)[1]
    assert not body.startswith(JSON_PREFIX)
    assert len(parse_search_response(body).items) == 7


def test_garbage_raises_parse_error():
    with pytest.raises(SearchParseError):
        parse_search_response("<html>not json</html>")


def test_missing_search_list_yields_empty_page():
    page = parse_search_response('{"result": {"totalCount": 0}}')
    assert page.is_empty is True
    assert page.total_count == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_search_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.search'`

- [ ] **Step 3: `engine/models.py` 구현**

```python
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

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def is_capped(self) -> bool:
        """totalCount가 상한에 걸렸는가. 진행률을 '1000+'로 표기해야 한다."""
        return self.total_count >= TOTAL_COUNT_CAP


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
```

- [ ] **Step 4: `engine/search.py` 구현 (파서 부분만)**

```python
"""블로그 검색 — 응답 파서(순수)와 페이지네이션(HTTP).

엔드포인트와 응답 구조는 2026-08-30 실측 결과다. 스펙 §3.1 참조.
파서를 순수 함수로 분리해 두었기 때문에 브라우저도 네트워크도 없이 테스트된다.
"""
from __future__ import annotations

import json

from engine.models import SearchItem, SearchPage

JSON_PREFIX = ")]}',"


class SearchParseError(Exception):
    """검색 응답이 예상한 JSON 형태가 아니다."""


def parse_search_response(raw: str | bytes) -> SearchPage:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    text = text.lstrip()
    if text.startswith(JSON_PREFIX):
        text = text[len(JSON_PREFIX):]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SearchParseError(f"JSON 파싱 실패: {exc}") from exc

    result = payload.get("result")
    if not isinstance(result, dict):
        raise SearchParseError("응답에 result 객체가 없습니다.")

    items = [
        SearchItem(
            blog_id=str(row.get("domainIdOrBlogId", "")).strip(),
            log_no=str(row.get("logNo", "")).strip(),
            title=str(row.get("title", "")),
            blog_name=str(row.get("blogName", "")),
            add_date_ms=int(row.get("addDate") or 0),
        )
        for row in (result.get("searchList") or [])
        if str(row.get("domainIdOrBlogId", "")).strip()
        and str(row.get("logNo", "")).strip()
    ]

    return SearchPage(
        items=items,
        total_count=int(result.get("totalCount") or 0),
        per_page=int(result.get("pagePerCount") or 7),
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_search_parse.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: 커밋**

```bash
git add engine/models.py engine/search.py tests/test_search_parse.py
git commit -m "feat: parse Naver blog search JSON responses"
```

---

## Task 4: 검색 페이지네이션 (HTTP)

**Files:**
- Modify: `engine/search.py` (파서 아래에 추가)
- Test: `tests/test_search_paginate.py`

**Interfaces:**
- Consumes: `engine.search.parse_search_response`, `engine.models.SearchPage`
- Produces:
  - `engine.search.SEARCH_URL: str`, `engine.search.SEARCH_HEADERS: dict[str, str]`
  - `engine.search.build_params(query: str, start_date: str, end_date: str, page: int) -> dict[str, str]`
  - `engine.search.SearchClient` — `__init__(self, http: httpx.AsyncClient)`, `async fetch_page(query, start_date, end_date, page) -> SearchPage`, `async iter_pages(query, start_date, end_date, first_page: int = 1) -> AsyncIterator[tuple[int, SearchPage]]`

`iter_pages`는 **빈 페이지를 만나면 중단**한다(결정 2). 143페이지 상한을 코드에 박지 않는다 — 네이버가 상한을 바꿔도 그대로 동작하고, 좁은 키워드는 자연히 더 일찍 끝난다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_search_paginate.py`**

```python
import httpx
import pytest

from engine.search import SearchClient, build_params

FIXTURE_PAGE = (
    ")]}',\n"
    '{"result":{"totalCount":20,"pagePerCount":7,"searchList":['
    '{"domainIdOrBlogId":"blog_a","logNo":111,"title":"t","blogName":"b","addDate":1},'
    '{"domainIdOrBlogId":"blog_b","logNo":222,"title":"t","blogName":"b","addDate":2}]}}'
)
FIXTURE_EMPTY = ")]}',\n" '{"result":{"totalCount":0,"pagePerCount":7,"searchList":[]}}'


def test_build_params_uses_period_range_and_recentdate():
    p = build_params("헬스장 -협찬", "2026-08-29", "2026-08-30", 3)
    assert p["keyword"] == "헬스장 -협찬"
    assert p["currentPage"] == "3"
    assert p["countPerPage"] == "7"
    assert p["orderBy"] == "recentdate"
    assert p["rangeType"] == "PERIOD"
    assert p["startDate"] == "2026-08-29"
    assert p["endDate"] == "2026-08-30"


async def test_fetch_page_sends_referer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("referer")
        return httpx.Response(200, text=FIXTURE_PAGE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        page = await SearchClient(http).fetch_page("k", "2026-08-29", "2026-08-30", 1)

    assert "section.blog.naver.com" in seen["referer"]
    assert [i.blog_id for i in page.items] == ["blog_a", "blog_b"]


async def test_iter_pages_stops_on_first_empty_page():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["currentPage"])
        calls.append(page)
        return httpx.Response(200, text=FIXTURE_PAGE if page <= 2 else FIXTURE_EMPTY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        pages = [n async for n, _ in SearchClient(http).iter_pages("k", "a", "b")]

    assert pages == [1, 2]
    assert calls == [1, 2, 3]      # 3페이지를 받아보고 비어서 멈춘다


async def test_http_error_raises():
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await SearchClient(http).fetch_page("k", "a", "b", 1)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_search_paginate.py -v`
Expected: FAIL — `ImportError: cannot import name 'SearchClient'`

- [ ] **Step 3: `engine/search.py` 하단에 추가**

```python
from collections.abc import AsyncIterator

import httpx

SEARCH_URL = "https://section.blog.naver.com/ajax/SearchList.naver"
SEARCH_HEADERS = {
    "Referer": "https://section.blog.naver.com/Search/Post.naver",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
PER_PAGE = 7


def build_params(query: str, start_date: str, end_date: str, page: int) -> dict[str, str]:
    return {
        "countPerPage": str(PER_PAGE),
        "currentPage": str(page),
        "keyword": query,
        "type": "post",
        "orderBy": "recentdate",
        "rangeType": "PERIOD",
        "startDate": start_date,
        "endDate": end_date,
    }


class SearchClient:
    """검색 API 호출. Referer 헤더가 없으면 응답이 달라지므로 항상 붙인다."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_page(
        self, query: str, start_date: str, end_date: str, page: int
    ) -> SearchPage:
        response = await self._http.get(
            SEARCH_URL,
            params=build_params(query, start_date, end_date, page),
            headers=SEARCH_HEADERS,
        )
        response.raise_for_status()
        return parse_search_response(response.text)

    async def iter_pages(
        self, query: str, start_date: str, end_date: str, first_page: int = 1
    ) -> AsyncIterator[tuple[int, SearchPage]]:
        """빈 페이지를 만날 때까지 페이지를 올린다 (결정 2).

        143페이지 상한을 코드에 박지 않는다. 네이버가 상한을 바꿔도 동작하고,
        좁은 키워드는 자연히 더 일찍 끝난다.
        """
        page = first_page
        while True:
            result = await self.fetch_page(query, start_date, end_date, page)
            if result.is_empty:
                return
            yield page, result
            page += 1
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_search_paginate.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/search.py tests/test_search_paginate.py
git commit -m "feat: paginate search results until an empty page"
```

---

## Task 5: posts.py — RSS 최신 글 목록

**Files:**
- Create: `engine/posts.py`
- Test: `tests/test_posts_parse.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `engine.posts.rss_url(blog_id: str) -> str`
  - `engine.posts.parse_rss_log_nos(xml: bytes | str) -> list[str]`
  - `engine.posts.PostsClient` — `__init__(self, http: httpx.AsyncClient)`, `async recent_log_nos(blog_id: str, limit: int) -> list[str]` (실패 시 빈 리스트)

**주의:** `<link>`가 CDATA로 감싸여 있다. 정규식으로 원문을 훑으면 놓치고, XML 파서를 쓰면 CDATA가 투명하게 처리된다. 설계 검증 중 실제로 이 실수를 했다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_posts_parse.py`**

```python
from pathlib import Path

import httpx

from engine.posts import PostsClient, parse_rss_log_nos, rss_url

FIXTURES = Path(__file__).parent / "fixtures"

CDATA_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title><![CDATA[blog]]></title>
  <link><![CDATA[https://blog.naver.com/someone?fromRss=true]]></link>
  <item><link><![CDATA[https://blog.naver.com/someone/224395288365]]></link></item>
  <item><link><![CDATA[https://blog.naver.com/someone/224387801453?fromRss=true]]></link></item>
</channel></rss>"""


def test_rss_url():
    assert rss_url("someone") == "https://rss.blog.naver.com/someone.xml"


def test_parses_log_nos_out_of_cdata_wrapped_links():
    assert parse_rss_log_nos(CDATA_RSS) == ["224395288365", "224387801453"]


def test_channel_link_is_not_mistaken_for_a_post():
    """channel/link에는 글 번호가 없다. item만 읽어야 한다."""
    assert "someone" not in parse_rss_log_nos(CDATA_RSS)


def test_real_fixture_yields_log_nos():
    log_nos = parse_rss_log_nos((FIXTURES / "rss_sample.xml").read_bytes())
    assert len(log_nos) >= 5
    assert all(n.isdigit() for n in log_nos)


def test_malformed_xml_returns_empty_list():
    assert parse_rss_log_nos(b"<not xml") == []


async def test_client_truncates_to_limit():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=CDATA_RSS))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await PostsClient(http).recent_log_nos("someone", 1) == ["224395288365"]


async def test_client_returns_empty_on_http_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await PostsClient(http).recent_log_nos("someone", 3) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_posts_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.posts'`

- [ ] **Step 3: `engine/posts.py` 구현**

```python
"""블로그의 최신 글 목록 — RSS.

레거시는 PostList.naver를 브라우저로 열어 "목록열기" 버튼을 누르고 테이블
XPath를 훑었다. RSS는 같은 정보를 마크업 변경에 면역인 형태로 준다.
실측(2026-08-30): 대상 7곳 전부 성공, 13~50개 반환.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_LOG_NO = re.compile(r"/(\d+)(?:\?|$)")


def rss_url(blog_id: str) -> str:
    return f"https://rss.blog.naver.com/{blog_id}.xml"


def parse_rss_log_nos(xml: bytes | str) -> list[str]:
    """channel/item/link에서 글 번호를 최신순으로 뽑는다.

    <link>가 CDATA로 감싸여 있으므로 원문을 정규식으로 훑으면 안 된다.
    XML 파서는 CDATA를 투명하게 처리한다.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    log_nos: list[str] = []
    for item in root.iterfind("./channel/item"):
        link = (item.findtext("link") or "").strip()
        match = _LOG_NO.search(link)
        if match:
            log_nos.append(match.group(1))
    return log_nos


class PostsClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def recent_log_nos(self, blog_id: str, limit: int) -> list[str]:
        """실패는 예외가 아니라 빈 리스트다 — 호출자가 seed_log_no로 폴백한다."""
        try:
            response = await self._http.get(rss_url(blog_id), headers=RSS_HEADERS)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return parse_rss_log_nos(response.content)[:limit]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_posts_parse.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/posts.py tests/test_posts_parse.py
git commit -m "feat: read recent post ids from blog RSS"
```

---

## Task 6: history.py — 계정별 방문 기록

**Files:**
- Create: `engine/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `engine.history.History` — `__init__(self, db_path: Path)`, `close()`
  - `was_visited(account: str, blog_id: str) -> bool`
  - `record_visit(account, blog_id, run_id, keyword, likes_ok, likes_tried, outcome) -> None`
  - `start_run(run_id: str, account: str, keywords: list[str]) -> None`
  - `finish_run(run_id: str, blogs_done: int, likes_ok: int, stop_reason: str) -> None`
  - `visited_count(account: str) -> int`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_history.py`**

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.history'`

- [ ] **Step 3: `engine/history.py` 구현**

```python
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
    stop_reason  TEXT
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
        self.connection.commit()

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

    def start_run(self, run_id: str, account: str, keywords: list[str]) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO runs
                 (run_id, started_at, account, keywords, blogs_done, likes_ok)
               VALUES (?, ?, ?, ?, 0, 0)""",
            (run_id, _now(), account, json.dumps(keywords, ensure_ascii=False)),
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_history.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/history.py tests/test_history.py
git commit -m "feat: persist visit history scoped to the logged-in account"
```

---

## Task 7: ratelimit.py — 간격 페이싱 + 지터

**스펙과의 의도적 차이:** 스펙은 "토큰 버킷"이라고 썼으나, 토큰 버킷은 유휴 시간에 토큰이 쌓여 **버스트를 허용**한다. 계정 안전 관점에서 버스트는 바람직하지 않으므로 **간격 기반 페이싱**으로 구현한다. 평균 속도는 같고 순간 속도만 제한된다.

**Files:**
- Create: `engine/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `engine.ratelimit.RateLimiter` — `__init__(self, per_minute: float, *, jitter: tuple[float, float] = (0.6, 1.6), clock=None, sleeper=None, rng=None)`
  - `async acquire() -> None`
  - `async pause_between_blogs() -> None` — 블로그 전환 시 조금 더 쉰다
  - 속성 `base_interval: float`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_ratelimit.py`**

```python
import pytest

from engine.ratelimit import RateLimiter


class FakeClock:
    """가짜 시계 — 잠든 만큼 시간이 흐른다. 테스트가 결정론적이 된다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FixedRng:
    def __init__(self, value: float) -> None:
        self._value = value

    def uniform(self, a: float, b: float) -> float:
        return self._value


def _limiter(per_minute=6.0, jitter_value=1.0):
    clock = FakeClock()
    limiter = RateLimiter(
        per_minute,
        clock=clock.time,
        sleeper=clock.sleep,
        rng=FixedRng(jitter_value),
    )
    return limiter, clock


def test_base_interval_derives_from_per_minute():
    limiter, _ = _limiter(per_minute=6.0)
    assert limiter.base_interval == pytest.approx(10.0)


async def test_first_acquire_does_not_sleep():
    limiter, clock = _limiter()
    await limiter.acquire()
    assert clock.slept == []


async def test_second_acquire_waits_the_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.0)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.slept == [pytest.approx(10.0)]


async def test_jitter_scales_the_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.5)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.slept == [pytest.approx(15.0)]


async def test_no_burst_after_idle_time():
    """유휴 시간이 길어도 다음 호출은 여전히 즉시 1회만 허용된다."""
    limiter, clock = _limiter(per_minute=6.0)
    await limiter.acquire()
    clock.now += 600           # 10분 유휴
    await limiter.acquire()    # 밀린 만큼 몰아치지 않는다
    await limiter.acquire()
    assert clock.slept == [pytest.approx(10.0)]


async def test_pause_between_blogs_is_longer_than_one_interval():
    limiter, clock = _limiter(per_minute=6.0, jitter_value=1.0)
    await limiter.pause_between_blogs()
    assert clock.slept and clock.slept[0] > limiter.base_interval


def test_invalid_rate_rejected():
    with pytest.raises(ValueError):
        RateLimiter(0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.ratelimit'`

- [ ] **Step 3: `engine/ratelimit.py` 구현**

```python
"""계정 단위 속도 제한.

워커 전원이 이 인스턴스 하나를 공유한다. 단일 이벤트 루프이므로 락이 필요 없다.

토큰 버킷이 아니라 간격 페이싱이다. 토큰 버킷은 유휴 시간에 토큰이 쌓여
버스트를 허용하는데, 계정 안전 관점에서 "한동안 쉬었으니 몰아서 누른다"는
정확히 피해야 할 패턴이다.

지터를 거는 이유: 정확히 일정한 간격은 그 규칙성 자체가 봇 신호다.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

BLOCK_PAUSE_MULTIPLIER = 1.8    # 블로그 전환 시 추가 휴식


class RateLimiter:
    def __init__(
        self,
        per_minute: float,
        *,
        jitter: tuple[float, float] = (0.6, 1.6),
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute은 0보다 커야 합니다.")
        self.base_interval = 60.0 / per_minute
        self._jitter = jitter
        self._clock = clock or time.monotonic
        self._sleep = sleeper or asyncio.sleep
        self._rng = rng or random.Random()
        self._next_allowed: float | None = None

    def _interval(self, multiplier: float = 1.0) -> float:
        return self.base_interval * multiplier * self._rng.uniform(*self._jitter)

    async def _wait_until_allowed(self, multiplier: float) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            await self._sleep(self._next_allowed - now)
            now = self._clock()
        # 유휴 시간이 길었더라도 다음 허용 시각은 '지금' 기준이다 (버스트 방지)
        self._next_allowed = now + self._interval(multiplier)

    async def acquire(self) -> None:
        """공감 1건에 대한 허가. 필요하면 대기한다."""
        await self._wait_until_allowed(1.0)

    async def pause_between_blogs(self) -> None:
        """블로그를 바꿀 때는 조금 더 쉰다 — 사람은 그렇게 움직인다."""
        await self._wait_until_allowed(BLOCK_PAUSE_MULTIPLIER)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_ratelimit.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: pace likes per account with jittered intervals"
```

---

## Task 8: safety.py — 차단 감지

**Files:**
- Create: `engine/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Consumes: `engine.models.LikeOutcome`
- Produces:
  - `engine.safety.BlockDetector` — `__init__(self, *, consecutive_failures: int = 5, window: int = 20, min_success_rate: float = 0.3)`
  - `record(outcome: LikeOutcome) -> str | None` — 중단해야 하면 사유 문자열, 아니면 `None`

`ALREADY_LIKED`와 `NO_BUTTON`은 정상 상황이므로 성공에도 실패에도 넣지 않는다. `BLOCKED`는 즉시. 성공률 판정은 창이 다 찬 뒤에만 한다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_safety.py`**

```python
from engine.models import LikeOutcome as O
from engine.safety import BlockDetector


def test_blocked_outcome_trips_immediately():
    d = BlockDetector()
    assert d.record(O.BLOCKED) is not None


def test_successes_never_trip():
    d = BlockDetector()
    for _ in range(100):
        assert d.record(O.SUCCESS) is None


def test_consecutive_failures_trip():
    d = BlockDetector(consecutive_failures=3)
    assert d.record(O.ERROR) is None
    assert d.record(O.TIMEOUT) is None
    assert d.record(O.ERROR) is not None


def test_a_success_resets_the_consecutive_counter():
    d = BlockDetector(consecutive_failures=3)
    d.record(O.ERROR)
    d.record(O.ERROR)
    d.record(O.SUCCESS)
    assert d.record(O.ERROR) is None


def test_neutral_outcomes_do_not_count_as_failures():
    """이미 공감했거나 버튼이 없는 것은 정상 상황이다."""
    d = BlockDetector(consecutive_failures=3)
    for _ in range(50):
        assert d.record(O.ALREADY_LIKED) is None
        assert d.record(O.NO_BUTTON) is None


def test_neutral_outcomes_do_not_reset_the_consecutive_counter():
    d = BlockDetector(consecutive_failures=3)
    d.record(O.ERROR)
    d.record(O.ALREADY_LIKED)
    d.record(O.ERROR)
    assert d.record(O.ERROR) is not None


def test_success_rate_only_evaluated_once_window_is_full():
    d = BlockDetector(consecutive_failures=99, window=10, min_success_rate=0.5)
    # 실패 4 + 성공 5 = 9건, 창이 아직 안 참
    for _ in range(4):
        assert d.record(O.ERROR) is None
    for _ in range(5):
        assert d.record(O.SUCCESS) is None


def test_low_success_rate_over_full_window_trips():
    d = BlockDetector(consecutive_failures=99, window=10, min_success_rate=0.5)
    tripped = None
    for i in range(10):
        tripped = d.record(O.SUCCESS if i < 3 else O.ERROR)
    assert tripped is not None


def test_reason_names_the_signal():
    d = BlockDetector(consecutive_failures=2)
    d.record(O.ERROR)
    assert "연속" in d.record(O.ERROR)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_safety.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.safety'`

- [ ] **Step 3: `engine/safety.py` 구현**

```python
"""차단 감지.

네이버가 제한을 걸 때 정확히 어떤 신호를 주는지는 확인되지 않았다. 그래서
안전은 간접 신호가 책임진다 — 네이버가 어떤 방식으로 막든 "갑자기 안 되기
시작함"은 공통이기 때문이다. 네이버의 구현을 몰라도 동작하고, 그 구현이
바뀌어도 계속 동작한다. 스펙 §7.2.
"""
from __future__ import annotations

from collections import deque

from engine.models import LikeOutcome

FAILURES = {
    LikeOutcome.TIMEOUT,
    LikeOutcome.ERROR,
    LikeOutcome.BLOCKED,
    LikeOutcome.NOT_LOGGED_IN,
}
SUCCESSES = {LikeOutcome.SUCCESS}
# ALREADY_LIKED / NO_BUTTON은 정상 상황이므로 어느 쪽에도 넣지 않는다.


class BlockDetector:
    def __init__(
        self,
        *,
        consecutive_failures: int = 5,
        window: int = 20,
        min_success_rate: float = 0.3,
    ) -> None:
        self._limit = consecutive_failures
        self._window_size = window
        self._min_rate = min_success_rate
        self._streak = 0
        self._window: deque[bool] = deque(maxlen=window)

    def record(self, outcome: LikeOutcome) -> str | None:
        """중단해야 하면 사유를, 아니면 None을 반환한다."""
        if outcome is LikeOutcome.BLOCKED:
            return "네이버가 제한을 건 것으로 보입니다 (직접 신호)."

        if outcome in FAILURES:
            self._streak += 1
            self._window.append(False)
        elif outcome in SUCCESSES:
            self._streak = 0
            self._window.append(True)
        else:
            return None    # 중립 — 연속 카운터도 창도 건드리지 않는다

        if self._streak >= self._limit:
            return f"{self._streak}건 연속 실패했습니다."

        if len(self._window) == self._window_size:
            rate = sum(self._window) / self._window_size
            if rate < self._min_rate:
                return f"최근 {self._window_size}건의 성공률이 {rate:.0%}로 떨어졌습니다."

        return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_safety.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/safety.py tests/test_safety.py
git commit -m "feat: detect blocking from consecutive failures and success rate"
```

---

## Task 9: session.py — 브라우저 · 로그인 · 계정별 세션

레거시 결함 3(로그인 실패 미감지)을 여기서 끝낸다.

**Files:**
- Create: `engine/session.py`
- Test: `tests/test_session_helpers.py` (순수 부분만)

**Interfaces:**
- Consumes: `engine.paths.AppPaths`
- Produces:
  - 예외: `LoginError(Exception)`, `CaptchaRequired(LoginError)`, `TwoFactorRequired(LoginError)`, `BadCredentials(LoginError)`, `SessionExpired(LoginError)`
  - `engine.session.classify_login_page(url: str, page_text: str) -> type[LoginError] | None` — 순수 함수
  - `engine.session.encrypt_bytes(data: bytes) -> bytes` / `decrypt_bytes(blob: bytes) -> bytes`
  - `engine.session.BrowserSession` — `async open(account: str, password_supplier, *, headless: bool = False) -> BrowserSession`, 속성 `page`, `async close()`

**Playwright 로그인 주의:** `fill()`은 자동화로 탐지되기 쉽다. 레거시가 클립보드 붙여넣기를 쓴 이유가 그것이다. Playwright에서는 `page.keyboard.insert_text()`가 같은 효과(키 이벤트 없이 값 삽입)를 낸다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_session_helpers.py`**

```python
import pytest

from engine.session import (
    BadCredentials,
    CaptchaRequired,
    TwoFactorRequired,
    classify_login_page,
    decrypt_bytes,
    encrypt_bytes,
)


def test_successful_landing_classifies_as_none():
    assert classify_login_page("https://www.naver.com/", "") is None


def test_captcha_page_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login", "자동입력 방지 문자를 입력해 주세요"
    ) is CaptchaRequired


def test_two_factor_page_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login?mode=number", "2단계 인증"
    ) is TwoFactorRequired


def test_bad_credentials_is_recognized():
    assert classify_login_page(
        "https://nid.naver.com/nidlogin.login",
        "아이디 또는 비밀번호를 잘못 입력했습니다",
    ) is BadCredentials


def test_still_on_login_page_without_a_known_message_is_generic_failure():
    result = classify_login_page("https://nid.naver.com/nidlogin.login", "무언가 다른 화면")
    assert result is not None
    assert issubclass(result, Exception)


def test_encrypt_round_trips():
    payload = b'{"cookies": [{"name": "NID_AUT"}]}'
    assert decrypt_bytes(encrypt_bytes(payload)) == payload


def test_encrypted_blob_does_not_contain_the_plaintext():
    payload = b"NID_AUT_secret_value"
    assert payload not in encrypt_bytes(payload)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_session_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.session'`

- [ ] **Step 3: `engine/session.py` 구현**

```python
"""브라우저 기동 · 로그인 · 계정별 세션 저장.

레거시는 로그인 예외를 로그만 남기고 진행해서, driver가 살아 있으면 로그아웃
상태로 전 과정을 돌며 모든 공감이 실패했다 (결함 3). 여기서는 로그인이
확인되지 않으면 예외를 올리고 워커가 시작조차 하지 않는다.

세션 파일은 네이버 세션 쿠키다 — 훔치면 비밀번호 없이 로그인된다. 자격증명을
키링에 넣고 세션을 평문으로 두면 보호가 반감되므로 암호화해 저장한다.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from engine.paths import AppPaths

LOGIN_URL = (
    "https://nid.naver.com/nidlogin.login?mode=form"
    "&url=https%3A%2F%2Fwww.naver.com&locale=ko_KR&svctype=1"
)
LOGIN_HOST = "nid.naver.com"


class LoginError(Exception):
    """로그인이 확인되지 않았다."""


class CaptchaRequired(LoginError):
    pass


class TwoFactorRequired(LoginError):
    pass


class BadCredentials(LoginError):
    pass


class SessionExpired(LoginError):
    pass


_CAPTCHA_HINTS = ("자동입력 방지", "captcha", "보안 문자")
_TWO_FACTOR_HINTS = ("2단계 인증", "일회용 번호", "인증번호를 입력")
_BAD_CRED_HINTS = ("아이디 또는 비밀번호", "다시 확인해주세요", "로그인 정보가")


def classify_login_page(url: str, page_text: str) -> type[LoginError] | None:
    """로그인 시도 후의 화면을 보고 실패 유형을 가른다.

    레거시는 캡차·2차인증·비밀번호 오류를 뭉뚱그렸다. 대응이 각각 다르므로
    구분해야 한다. 문구는 네이버가 바꿀 수 있으므로, 어느 힌트에도 걸리지
    않아도 로그인 호스트에 남아 있으면 일반 LoginError로 처리한다.
    """
    if LOGIN_HOST not in url:
        return None

    haystack = page_text.lower()
    if any(h.lower() in haystack for h in _CAPTCHA_HINTS):
        return CaptchaRequired
    if any(h.lower() in haystack for h in _TWO_FACTOR_HINTS):
        return TwoFactorRequired
    if any(h.lower() in haystack for h in _BAD_CRED_HINTS):
        return BadCredentials
    return LoginError


def encrypt_bytes(data: bytes) -> bytes:
    """Windows는 DPAPI(로그인 사용자 계정에 묶임), 그 외는 그대로 둔다.

    비-Windows에서는 호출자가 파일 권한 0600으로 보호한다.
    """
    if sys.platform == "win32":
        import win32crypt

        return win32crypt.CryptProtectData(data, None, None, None, None, 0)
    return data


def decrypt_bytes(blob: bytes) -> bytes:
    if sys.platform == "win32":
        import win32crypt

        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    return blob


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(data))
    if sys.platform != "win32":
        path.chmod(0o600)


class BrowserSession:
    """브라우저 컨텍스트 하나 + 탭 하나. 공감은 단일 스트림이다 (결정 4)."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    async def open(
        self,
        account: str,
        password_supplier: Callable[[], str],
        *,
        headless: bool = False,
    ) -> "BrowserSession":
        state_file = self._paths.session_file(account)
        storage_state = None
        if state_file.exists():
            try:
                storage_state = decrypt_bytes(state_file.read_bytes()).decode("utf-8")
            except Exception:
                storage_state = None    # 손상된 세션은 무시하고 새로 로그인한다

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        self._context = await self._browser.new_context(storage_state=storage_state)
        self.page = await self._context.new_page()

        if not await self._is_logged_in():
            await self._login(account, password_supplier())
            await self._save_state(state_file)
        return self

    async def _is_logged_in(self) -> bool:
        """실제로 로그인 상태인지 확인한다. 쿠키 존재만으로는 부족하다."""
        await self.page.goto("https://blog.naver.com/", wait_until="domcontentloaded")
        if LOGIN_HOST in self.page.url:
            return False
        cookies = await self._context.cookies()
        return any(c["name"] == "NID_AUT" for c in cookies)

    async def _login(self, account: str, password: str) -> None:
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # fill()은 탐지되기 쉽다. insert_text는 키 이벤트 없이 값을 넣는다
        # (레거시의 클립보드 붙여넣기와 같은 효과).
        await self.page.click("#id")
        await self.page.keyboard.insert_text(account)
        await self.page.click("#pw")
        await self.page.keyboard.insert_text(password)

        await self.page.click(".btn_login")
        await self.page.wait_for_load_state("domcontentloaded")

        body_text = await self.page.inner_text("body")
        failure = classify_login_page(self.page.url, body_text)
        if failure is not None:
            raise failure(f"로그인이 확인되지 않았습니다. 현재 주소: {self.page.url}")

        if not await self._is_logged_in():
            raise SessionExpired("로그인 직후 세션이 확인되지 않았습니다.")

    async def _save_state(self, state_file: Path) -> None:
        import json

        state = await self._context.storage_state()
        _write_secret(state_file, json.dumps(state).encode("utf-8"))

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_session_helpers.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/session.py tests/test_session_helpers.py
git commit -m "feat: verify login and store per-account sessions encrypted"
```

---

## Task 10: like.py — 공감 클릭

**Files:**
- Create: `engine/like.py`
- Test: `tests/test_like_logic.py` (순수 판정 부분)

**Interfaces:**
- Consumes: `engine.models.LikeOutcome`, `engine.session.LOGIN_HOST`
- Produces:
  - `engine.like.post_url(blog_id: str, log_no: str) -> str`
  - `engine.like.classify_button_state(class_attr: str | None) -> LikeOutcome | None` — `on`이면 `ALREADY_LIKED`, `off`면 `None`(누를 수 있음), 그 외 `ERROR`
  - `engine.like.press_like(page, blog_id: str, log_no: str, *, dry_run: bool = False) -> LikeOutcome`

**셀렉터 근거:** 레거시는 `//div[@class="post-btn post_btn2"]//a[contains(@class,"off")]`를 썼다. 네이버 공감 버튼은 `a.u_likeit_list_btn`이고 `off`/`on` 클래스가 상태를 나타낸다. 클릭 후 클래스가 `on`으로 바뀌는지 **확인**해야 한다 — 확인 없는 클릭은 거짓 성공을 만든다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_like_logic.py`**

```python
from engine.like import classify_button_state, post_url
from engine.models import LikeOutcome


def test_post_url():
    assert post_url("someone", "224395288365") == \
        "https://blog.naver.com/someone/224395288365"


def test_off_button_is_clickable():
    assert classify_button_state("u_likeit_list_btn _button off pcol2") is None


def test_on_button_means_already_liked():
    assert classify_button_state("u_likeit_list_btn _button on pcol2") \
        is LikeOutcome.ALREADY_LIKED


def test_missing_class_attribute_is_an_error():
    assert classify_button_state(None) is LikeOutcome.ERROR


def test_unknown_state_is_an_error():
    assert classify_button_state("u_likeit_list_btn _button") is LikeOutcome.ERROR


def test_on_takes_precedence_when_both_words_appear():
    """'off'가 다른 클래스명 조각에 섞여 들어와도 'on' 상태를 뒤집지 못한다."""
    assert classify_button_state("btn_off_wrap u_likeit_list_btn on") \
        is LikeOutcome.ALREADY_LIKED
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_like_logic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.like'`

- [ ] **Step 3: `engine/like.py` 구현**

```python
"""공감 클릭.

결과를 유형으로 반환한다. 레거시는 이 일곱 가지를 "공감 없음 or 이미 함" 한
줄로 뭉갰다. 유형을 나누는 이유는 표시가 아니라 대응이 각각 다르기 때문이다
(스펙 §7.1).

클릭 후 상태가 실제로 바뀌었는지 확인한다. 확인 없는 클릭은 거짓 성공을 만들고,
그러면 차단 감지가 무력해진다.
"""
from __future__ import annotations

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from engine.models import LikeOutcome
from engine.session import LOGIN_HOST

LIKE_BUTTON = "a.u_likeit_list_btn"
FRAME = "#mainFrame"
GOTO_TIMEOUT_MS = 15_000
BUTTON_TIMEOUT_MS = 6_000


def post_url(blog_id: str, log_no: str) -> str:
    return f"https://blog.naver.com/{blog_id}/{log_no}"


def classify_button_state(class_attr: str | None) -> LikeOutcome | None:
    """공감 버튼의 class에서 상태를 읽는다.

    None을 반환하면 '아직 누르지 않았고 누를 수 있다'는 뜻이다.
    """
    if not class_attr:
        return LikeOutcome.ERROR
    tokens = class_attr.split()
    if "on" in tokens:
        return LikeOutcome.ALREADY_LIKED
    if "off" in tokens:
        return None
    return LikeOutcome.ERROR


async def press_like(
    page: Page, blog_id: str, log_no: str, *, dry_run: bool = False
) -> LikeOutcome:
    try:
        await page.goto(
            post_url(blog_id, log_no),
            wait_until="domcontentloaded",
            timeout=GOTO_TIMEOUT_MS,
        )
    except PlaywrightTimeout:
        return LikeOutcome.TIMEOUT

    if LOGIN_HOST in page.url:
        return LikeOutcome.NOT_LOGGED_IN

    try:
        button = page.frame_locator(FRAME).locator(LIKE_BUTTON).first
        await button.wait_for(state="attached", timeout=BUTTON_TIMEOUT_MS)
    except PlaywrightTimeout:
        return LikeOutcome.NO_BUTTON
    except PlaywrightError:
        return LikeOutcome.ERROR

    try:
        state = classify_button_state(await button.get_attribute("class"))
        if state is not None:
            return state          # ALREADY_LIKED 또는 ERROR

        if dry_run:
            # 드라이런: 버튼을 찾는 데까지만. 클릭하지 않는다.
            return LikeOutcome.SUCCESS

        await button.click(timeout=BUTTON_TIMEOUT_MS)

        # 클릭이 실제로 반영됐는지 확인한다.
        after = classify_button_state(await button.get_attribute("class"))
        if after is LikeOutcome.ALREADY_LIKED:
            return LikeOutcome.SUCCESS
        return LikeOutcome.ERROR
    except PlaywrightTimeout:
        return LikeOutcome.TIMEOUT
    except PlaywrightError:
        return LikeOutcome.ERROR
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_like_logic.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/like.py tests/test_like_logic.py
git commit -m "feat: press like and verify the button state actually changed"
```

---

## Task 11: runner.py — 생산자/소비자 오케스트레이션

레거시 결함 6(정지 시 실적 유실)을 여기서 끝낸다. 어떤 경로로 끝나든 집계가 보존된다.

**Files:**
- Create: `engine/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `RunConfig`, `History`, `RateLimiter`, `BlockDetector`, `SearchClient`, `PostsClient`, `LikeOutcome`, `Target`, 이벤트 전부
- Produces:
  - `engine.runner.LikeFn` — `Callable[[str, str], Awaitable[LikeOutcome]]` 타입 별칭
  - `engine.runner.Runner` — `__init__(self, config, history, search, posts, like_fn, limiter, detector, emit, run_id)`
  - `async run() -> RunSummary`
  - `request_stop() -> None`

**의존성 주입:** `like_fn`을 함수로 받기 때문에 브라우저 없이 전체 오케스트레이션을 테스트할 수 있다. 4가지 종료 경로가 전부 검증 대상이다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_runner.py`**

```python
import asyncio

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
            yield index, SearchPage(items=items, total_count=99, per_page=7)


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

    assert summary.stop_reason == "user"
    assert summary.likes_ok >= 4          # 진행 중이던 블로그를 마친다
    assert summary.blogs_done >= 1


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.runner'`

- [ ] **Step 3: `engine/runner.py` 구현**

```python
"""생산자/소비자 오케스트레이션.

키워드마다 생산자 코루틴 하나가 검색 결과를 큐에 넣고, 소비자 코루틴 하나가
브라우저 탭 하나로 공감한다. 공감은 계정 단위 속도 제한에 묶이므로 소비자를
늘려도 총량이 늘지 않는다 (결정 4).

집계는 RunSummary 하나에 누적되고, 어떤 경로로 끝나든 그 값이 보고된다.
레거시처럼 return 0, 0, 0 하는 경로가 없다 (결함 6).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from engine.config import RunConfig
from engine.events import (
    Aborted,
    BlogVisited,
    Event,
    LikeResultEvent,
    LogLine,
    PageCollected,
    RunFinished,
    RunSummary,
    WorkerStarted,
)
from engine.history import History
from engine.models import LikeOutcome, Target
from engine.ratelimit import RateLimiter
from engine.safety import BlockDetector

LikeFn = Callable[[str, str], Awaitable[LikeOutcome]]

QUEUE_MAXSIZE = 50
_SENTINEL = object()


class Runner:
    def __init__(
        self,
        *,
        config: RunConfig,
        history: History,
        search,
        posts,
        like_fn: LikeFn,
        limiter: RateLimiter,
        detector: BlockDetector,
        emit: Callable[[Event], None],
        run_id: str,
    ) -> None:
        self._config = config
        self._history = history
        self._search = search
        self._posts = posts
        self._like = like_fn
        self._limiter = limiter
        self._detector = detector
        self._emit = emit
        self._run_id = run_id

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._seen: set[str] = set()
        self._stop = asyncio.Event()
        self._blogs_done = 0
        self._likes_ok = 0
        self._likes_tried = 0
        self._per_keyword: dict[str, int] = {}
        self._stop_reason = "exhausted"

    def request_stop(self) -> None:
        self._stop.set()

    # ---------------- 생산자 ----------------

    async def _produce(self, keyword: str) -> None:
        self._emit(WorkerStarted(keyword))
        query = self._config.search_query(keyword)
        try:
            async for page_no, page in self._search.iter_pages(
                query, self._config.start_date, self._config.end_date
            ):
                if self._stop.is_set():
                    return

                queued = 0
                for item in page.items:
                    if item.blog_id in self._seen:
                        continue
                    if self._history.was_visited(self._config.account, item.blog_id):
                        continue
                    self._seen.add(item.blog_id)
                    await self._queue.put(
                        Target(item.blog_id, keyword, item.log_no)
                    )
                    queued += 1

                self._emit(PageCollected(
                    keyword=keyword, page=page_no, found=len(page.items),
                    queued=queued, total_count=page.total_count,
                ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._emit(LogLine(keyword, f"검색 중 오류: {exc}"))

    # ---------------- 소비자 ----------------

    async def _consume(self) -> None:
        while True:
            target = await self._queue.get()
            if target is _SENTINEL:
                return
            if self._stop.is_set():
                self._stop_reason = "user"
                return

            finished = await self._visit(target)
            if finished:
                return

            if self._blogs_done >= self._config.blog_limit:
                self._stop_reason = "budget"
                return

            await self._limiter.pause_between_blogs()

    async def _visit(self, target: Target) -> bool:
        """블로그 하나를 처리한다. True를 반환하면 전체 중단이다."""
        log_nos = await self._posts.recent_log_nos(
            target.blog_id, self._config.likes_per_blog
        )
        if not log_nos:
            log_nos = [target.seed_log_no]

        ok = tried = 0
        for log_no in log_nos[: self._config.likes_per_blog]:
            await self._limiter.acquire()
            outcome = await self._like(target.blog_id, log_no)
            tried += 1
            self._likes_tried += 1
            self._emit(LikeResultEvent(target.blog_id, log_no, outcome.value))

            if outcome is LikeOutcome.SUCCESS:
                ok += 1
                self._likes_ok += 1

            reason = self._detector.record(outcome)
            if reason is not None:
                self._record(target, ok, tried)
                self._stop_reason = (
                    "blocked" if outcome is LikeOutcome.BLOCKED else "error"
                )
                self._emit(Aborted(reason))
                return True

        self._record(target, ok, tried)
        return False

    def _record(self, target: Target, ok: int, tried: int) -> None:
        outcome = "liked" if ok else "no_like"
        self._history.record_visit(
            self._config.account, target.blog_id, self._run_id,
            target.keyword, ok, tried, outcome,
        )
        self._blogs_done += 1
        self._per_keyword[target.keyword] = self._per_keyword.get(target.keyword, 0) + 1
        self._emit(BlogVisited(target.keyword, target.blog_id, ok, tried))

    # ---------------- 진입점 ----------------

    async def run(self) -> RunSummary:
        self._history.start_run(
            self._run_id, self._config.account, self._config.keywords
        )

        producers = [
            asyncio.create_task(self._produce(k)) for k in self._config.keywords
        ]
        consumer = asyncio.create_task(self._consume())

        async def close_queue_when_producers_done() -> None:
            await asyncio.gather(*producers, return_exceptions=True)
            await self._queue.put(_SENTINEL)

        closer = asyncio.create_task(close_queue_when_producers_done())

        try:
            await consumer
        finally:
            for task in producers:
                task.cancel()
            closer.cancel()
            await asyncio.gather(*producers, closer, return_exceptions=True)

        if self._stop.is_set() and self._stop_reason == "exhausted":
            self._stop_reason = "user"

        summary = RunSummary(
            run_id=self._run_id,
            blogs_done=self._blogs_done,
            likes_ok=self._likes_ok,
            likes_tried=self._likes_tried,
            stop_reason=self._stop_reason,
            per_keyword=dict(self._per_keyword),
        )
        self._history.finish_run(
            self._run_id, summary.blogs_done, summary.likes_ok, summary.stop_reason
        )
        self._emit(RunFinished(summary))
        return summary
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_runner.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 전체 단위 테스트 확인**

Run: `python -m pytest -v`
Expected: PASS — 지금까지의 모든 테스트가 통과한다.

- [ ] **Step 6: 커밋**

```bash
git add engine/runner.py tests/test_runner.py
git commit -m "feat: orchestrate producers and consumer with all four stop paths"
```

---

## Task 12: 계약 테스트 + 드라이런 CLI

**Files:**
- Create: `tests/test_contract.py`
- Create: `tools/dryrun.py`

**Interfaces:**
- Consumes: `SearchClient`, `PostsClient`, `RunConfig`, `BrowserSession`, `press_like`
- Produces: `tools/dryrun.py` 실행 진입점 (라이브러리 인터페이스 없음)

계약 테스트는 기본 실행에서 제외된다(`addopts = "-m 'not contract'"`). `-m contract`로 명시적으로 돌린다.

- [ ] **Step 1: 계약 테스트 작성 — `tests/test_contract.py`**

```python
"""층 2 — 네이버에 실제 요청을 보낸다. 비로그인이므로 계정 위험은 없다.

네이버가 바뀌었는지 감지하는 조기 경보다. 레거시의 결함 1이 반년 넘게
방치된 이유가 이런 감지 수단의 부재였다.

    python -m pytest -m contract -v
"""
import httpx
import pytest

from engine.posts import PostsClient
from engine.search import SearchClient

pytestmark = pytest.mark.contract

KEYWORD = "헬스장"


async def test_search_api_still_returns_expected_fields():
    async with httpx.AsyncClient(timeout=20) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)

    assert page.per_page == 7, "pagePerCount가 7이 아닙니다 — 페이지 크기가 바뀌었습니다."
    assert page.items, "searchList가 비어 있습니다."
    first = page.items[0]
    assert first.blog_id and first.log_no.isdigit()


async def test_total_count_cap_is_still_1000():
    async with httpx.AsyncClient(timeout=20) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)
    assert page.total_count == 1000, (
        f"넓은 키워드의 totalCount가 {page.total_count}입니다 — 상한이 바뀌었을 수 있습니다."
    )


async def test_pagination_still_ends_after_page_143():
    async with httpx.AsyncClient(timeout=20) as http:
        client = SearchClient(http)
        last = await client.fetch_page(KEYWORD, "", "", 143)
        past = await client.fetch_page(KEYWORD, "", "", 144)

    assert not last.is_empty, "143페이지가 비었습니다 — 상한이 앞당겨졌습니다."
    assert past.is_empty, "144페이지에 결과가 있습니다 — 상한이 늘었습니다."


async def test_rss_still_yields_post_ids_for_real_blogs():
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page = await SearchClient(http).fetch_page(KEYWORD, "", "", 1)
        posts = PostsClient(http)
        results = [
            await posts.recent_log_nos(item.blog_id, 5) for item in page.items[:5]
        ]

    succeeded = sum(1 for r in results if r)
    assert succeeded >= 3, f"RSS 성공 {succeeded}/5 — RSS 제공이 줄었을 수 있습니다."
```

- [ ] **Step 2: 계약 테스트 실행**

Run: `python -m pytest -m contract -v`
Expected: PASS (4 passed). 실패하면 네이버가 바뀐 것이므로, 실패 메시지를 근거로 스펙 §3을 갱신한다.

- [ ] **Step 3: 드라이런 CLI 작성 — `tools/dryrun.py`**

```python
"""드라이런 — 로그인하고, 글을 열고, 공감 버튼을 찾는 데까지만 한다.

클릭하지 않으므로 부작용이 없다. 셀렉터 유효성 · iframe 전환 · 로그인 생존을
계정 위험 없이 확인한다. 새 키워드를 본격 실행하기 전에 어떤 블로그가 잡히는지
미리 보는 용도로도 쓴다.

    python tools/dryrun.py <네이버ID> <키워드> [--blogs 5]
"""
from __future__ import annotations

import argparse
import asyncio
import getpass

import httpx

from engine.like import press_like
from engine.models import LikeOutcome
from engine.paths import AppPaths
from engine.posts import PostsClient
from engine.search import SearchClient
from engine.session import BrowserSession
from engine.config import default_dates


async def main_async(account: str, keyword: str, blogs: int) -> None:
    start, end = default_dates()
    paths = AppPaths.for_app()
    paths.ensure()

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        page = await SearchClient(http).fetch_page(keyword, start, end, 1)
        print(f"검색 '{keyword}' {start}~{end}: "
              f"{page.total_count}{'+' if page.is_capped else ''}건, "
              f"1페이지 {len(page.items)}건")

        targets = page.items[:blogs]
        posts = PostsClient(http)
        plans = [(t.blog_id, await posts.recent_log_nos(t.blog_id, 3) or [t.log_no])
                 for t in targets]

    for blog_id, log_nos in plans:
        print(f"  {blog_id:24s} 최신글 {log_nos}")

    session = BrowserSession(paths)
    await session.open(account, lambda: getpass.getpass(f"{account} 비밀번호: "))
    print("로그인 확인됨. 공감 버튼을 찾습니다 (클릭하지 않습니다).")

    try:
        for blog_id, log_nos in plans:
            outcome = await press_like(session.page, blog_id, log_nos[0], dry_run=True)
            mark = "찾음" if outcome is LikeOutcome.SUCCESS else outcome.value
            print(f"  {blog_id:24s} {log_nos[0]:>14s}  {mark}")
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="like-bot-v2 드라이런")
    parser.add_argument("account")
    parser.add_argument("keyword")
    parser.add_argument("--blogs", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main_async(args.account, args.keyword, args.blogs))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 드라이런 수동 확인**

Run: `python tools/dryrun.py <본인_네이버ID> 헬스장 --blogs 3`
Expected: 검색 건수와 블로그별 최신 글 목록이 출력되고, 브라우저가 뜨고, 세 블로그 모두 `찾음`이 나온다.

`no_button`이 나오면 `engine/like.py`의 `LIKE_BUTTON` 셀렉터를 실물 DOM에 맞게 고친다. `not_logged_in`이면 `engine/session.py`의 로그인 절차를 확인한다. **이 단계가 층 3의 검증이며, 여기서 셀렉터를 확정한다.**

- [ ] **Step 5: 커밋**

```bash
git add tests/test_contract.py tools/dryrun.py
git commit -m "test: add contract tests and a dry-run tool for selector checks"
```

---

## Task 13: bridge.py — asyncio ↔ Qt

**Files:**
- Create: `desktop/__init__.py`, `desktop/bridge.py`
- Test: `tests/test_bridge.py`

**Interfaces:**
- Consumes: `engine.events.Event`
- Produces:
  - `desktop.bridge.EngineBridge(QObject)` — 시그널 `event_received = pyqtSignal(object)`, `finished = pyqtSignal(object)`, `failed = pyqtSignal(str)`
  - `start(coro_factory: Callable[[Callable[[Event], None]], Awaitable[object]]) -> None`
  - `request_stop(callback: Callable[[], None]) -> None`
  - `is_running() -> bool`

엔진은 PyQt를 모른다. 브리지가 별도 스레드에서 이벤트 루프를 돌리고, 엔진이 부르는 `emit` 콜백을 Qt 시그널로 바꾼다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_bridge.py`**

```python
import asyncio
import time

import pytest

from desktop.bridge import EngineBridge
from engine.events import LogLine

QtCore = pytest.importorskip("PyQt6.QtCore")


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


def _pump(predicate, timeout=5.0):
    from PyQt6.QtWidgets import QApplication

    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_events_reach_qt_signals(app):
    received = []
    done = []

    bridge = EngineBridge()
    bridge.event_received.connect(received.append)
    bridge.finished.connect(done.append)

    async def work(emit):
        emit(LogLine("", "안녕"))
        await asyncio.sleep(0)
        emit(LogLine("", "끝"))
        return "summary"

    bridge.start(work)
    assert _pump(lambda: done)
    assert [e.text for e in received] == ["안녕", "끝"]
    assert done == ["summary"]


def test_engine_exception_surfaces_as_failed(app):
    errors = []
    bridge = EngineBridge()
    bridge.failed.connect(errors.append)

    async def boom(emit):
        raise RuntimeError("엔진 폭발")

    bridge.start(boom)
    assert _pump(lambda: errors)
    assert "엔진 폭발" in errors[0]


def test_is_running_is_false_after_completion(app):
    done = []
    bridge = EngineBridge()
    bridge.finished.connect(done.append)

    async def work(emit):
        return None

    bridge.start(work)
    assert _pump(lambda: done)
    assert bridge.is_running() is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'desktop.bridge'`

- [ ] **Step 3: `desktop/__init__.py` 생성 (빈 파일)**

```python
```

- [ ] **Step 4: `desktop/bridge.py` 구현**

```python
"""asyncio 엔진과 Qt UI 사이의 유일한 접점.

엔진은 PyQt를 모른다. 브리지가 별도 스레드에서 이벤트 루프를 돌리고, 엔진이
부르는 emit 콜백을 Qt 시그널로 바꾼다. 2단계 웹은 같은 이벤트를 웹소켓으로
중계하면 되므로 이 파일만 교체된다.
"""
from __future__ import annotations

import asyncio
import threading
import traceback
from collections.abc import Awaitable, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from engine.events import Event

CoroFactory = Callable[[Callable[[Event], None]], Awaitable[object]]


class EngineBridge(QObject):
    event_received = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, coro_factory: CoroFactory) -> None:
        if self.is_running():
            raise RuntimeError("이미 실행 중입니다.")

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(coro_factory(self.event_received.emit))
            except Exception:
                self.failed.emit(traceback.format_exc(limit=5))
            else:
                self.finished.emit(result)
            finally:
                loop.close()
                self._loop = None

        self._thread = threading.Thread(target=run, name="engine", daemon=True)
        self._thread.start()

    def request_stop(self, callback: Callable[[], None]) -> None:
        """엔진 루프 스레드 안에서 callback을 실행한다 (예: Runner.request_stop)."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(callback)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_bridge.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: 커밋**

```bash
git add desktop/__init__.py desktop/bridge.py tests/test_bridge.py
git commit -m "feat: bridge the asyncio engine to Qt signals"
```

---

## Task 14: 데스크톱 UI

**Files:**
- Create: `desktop/widgets.py`, `desktop/app.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: `EngineBridge`, `RunConfig`, `FieldError`, 이벤트 전부
- Produces:
  - `desktop.widgets.KeywordPanel(QGroupBox)` — `__init__(self, index: int)`, `keyword() -> str`, `set_running(bool)`, `append_log(str)`, `set_status(str)`, `set_alert(str)`
  - `desktop.app.MainWindow(QMainWindow)`, `desktop.app.main() -> None`

**레이아웃 요구:** 절대좌표를 쓰지 않는다. 레거시는 1323×890 고정이라 DPI·해상도에 취약했다. 상단 설정 폼 + 하단 키워드 패널 4개를 `QGridLayout`으로 배치한다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_widgets.py`**

```python
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from desktop.widgets import KeywordPanel


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


def test_panel_starts_idle_with_stop_disabled(app):
    panel = KeywordPanel(1)
    assert panel.start_button.isEnabled() is True
    assert panel.stop_button.isEnabled() is False


def test_set_running_swaps_button_states(app):
    panel = KeywordPanel(1)
    panel.set_running(True)
    assert panel.start_button.isEnabled() is False
    assert panel.stop_button.isEnabled() is True

    panel.set_running(False)
    assert panel.start_button.isEnabled() is True
    assert panel.stop_button.isEnabled() is False


def test_keyword_is_read_from_the_input(app):
    panel = KeywordPanel(1)
    panel.keyword_input.setText("  헬스장  ")
    assert panel.keyword() == "헬스장"


def test_append_log_accumulates(app):
    panel = KeywordPanel(1)
    panel.append_log("첫 줄")
    panel.append_log("둘째 줄")
    text = panel.log_view.toPlainText()
    assert "첫 줄" in text and "둘째 줄" in text


def test_alert_is_visible_only_when_set(app):
    """폴백·차단 경고는 눈에 띄어야 한다 — 조용히 잘리지 않게."""
    panel = KeywordPanel(1)
    assert panel.alert_label.isVisible() is False
    panel.set_alert("네이버 응답 형식이 바뀐 것 같습니다")
    assert panel.alert_label.text() != ""


def test_panel_uses_a_layout_not_fixed_geometry(app):
    """레거시의 절대좌표 배치를 반복하지 않는다."""
    panel = KeywordPanel(1)
    assert panel.layout() is not None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'desktop.widgets'`

- [ ] **Step 3: `desktop/widgets.py` 구현**

```python
"""키워드 패널 — 키워드 하나의 입력 · 상태 · 로그를 담는다.

레거시는 절대좌표(1323x890 고정)로 배치해 DPI와 해상도에 취약했고, 4개
패널이 상단의 같은 키워드를 공유해 사실상 같은 작업을 4번 돌렸다. 여기서는
패널마다 자기 키워드를 갖고 레이아웃 매니저로 배치한다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

MAX_LOG_BLOCKS = 2000


class KeywordPanel(QGroupBox):
    def __init__(self, index: int) -> None:
        super().__init__(f"키워드 {index}")
        self.index = index

        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("예: 교대 헬스장")

        self.start_button = QPushButton("▶ 실행")
        self.stop_button = QPushButton("■ 정지")
        self.stop_button.setEnabled(False)

        self.status_label = QLabel("대기 중")
        self.alert_label = QLabel("")
        self.alert_label.setWordWrap(True)
        self.alert_label.setStyleSheet(
            "background: #b00020; color: white; padding: 4px; border-radius: 3px;"
        )
        self.alert_label.hide()

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(MAX_LOG_BLOCKS)

        controls = QHBoxLayout()
        controls.addWidget(self.keyword_input, 1)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addWidget(self.alert_label)
        layout.addWidget(self.log_view, 1)

    def keyword(self) -> str:
        return self.keyword_input.text().strip()

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.keyword_input.setEnabled(not running)

    def append_log(self, text: str) -> None:
        self.log_view.append(text)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_alert(self, text: str) -> None:
        self.alert_label.setText(text)
        self.alert_label.setVisible(bool(text))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: `desktop/app.py` 구현**

```python
"""메인 창 — 설정 입력, 실행/정지, 키워드별 로그.

키워드 패널마다 자기 키워드를 갖지만, 실행은 계정 단위로 하나다. 공감이
계정 단위 속도 제한에 묶여 있어 여러 실행을 동시에 돌릴 이유가 없다 (결정 4).
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta

import httpx
import keyring
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from desktop.bridge import EngineBridge
from desktop.widgets import KeywordPanel
from engine.config import DEFAULTS, RunConfig, default_dates
from engine.events import (
    Aborted,
    BlogVisited,
    FallbackUsed,
    LikeResultEvent,
    LogLine,
    PageCollected,
    RunFinished,
    WorkerStarted,
)
from engine.history import History
from engine.like import press_like
from engine.paths import AppPaths
from engine.posts import PostsClient
from engine.ratelimit import RateLimiter
from engine.runner import Runner
from engine.safety import BlockDetector
from engine.search import SearchClient
from engine.session import BrowserSession, LoginError

KEYRING_SERVICE = "like-bot-v2"
PANEL_COUNT = 4


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("blog search & like")
        self.resize(1280, 860)

        self.paths = AppPaths.for_app()
        self.paths.ensure()
        self.bridge = EngineBridge()
        self.runner: Runner | None = None

        self.account_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        start, end = default_dates()
        self.start_date_input = QLineEdit(start)
        self.end_date_input = QLineEdit(end)

        self.exclude_input = QLineEdit()
        self.exclude_input.setPlaceholderText("제외 단어를 쉼표로 구분 (예: 협찬, 체험단)")

        self.blog_limit_input = QSpinBox()
        self.blog_limit_input.setRange(1, 100000)
        self.blog_limit_input.setValue(int(DEFAULTS["blog_limit"]))

        self.likes_input = QSpinBox()
        self.likes_input.setRange(1, 10)
        self.likes_input.setValue(int(DEFAULTS["likes_per_blog"]))

        self.rate_input = QDoubleSpinBox()
        self.rate_input.setRange(0.1, 60.0)
        self.rate_input.setDecimals(1)
        self.rate_input.setValue(float(DEFAULTS["likes_per_minute"]))
        self.rate_input.setSuffix(" 회/분")

        self.dry_run_input = QCheckBox("드라이런 (버튼만 확인, 클릭하지 않음)")

        self.run_button = QPushButton("▶ 전체 실행")
        self.stop_button = QPushButton("■ 정지")
        self.stop_button.setEnabled(False)
        self.summary_label = QLabel("대기 중")

        self.panels = [KeywordPanel(i + 1) for i in range(PANEL_COUNT)]
        for panel in self.panels:
            panel.start_button.hide()      # 실행은 계정 단위로 하나다
            panel.stop_button.hide()

        form = QFormLayout()
        form.addRow("네이버 ID", self.account_input)
        form.addRow("비밀번호", self.password_input)
        form.addRow("기간 시작", self.start_date_input)
        form.addRow("기간 종료", self.end_date_input)
        form.addRow("제외 단어", self.exclude_input)
        form.addRow("방문 블로그 상한", self.blog_limit_input)
        form.addRow("블로그당 공감 수", self.likes_input)
        form.addRow("속도", self.rate_input)
        form.addRow("", self.dry_run_input)

        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.summary_label)

        grid = QGridLayout()
        for i, panel in enumerate(self.panels):
            grid.addWidget(panel, 0, i)
            grid.setColumnStretch(i, 1)

        root = QVBoxLayout()
        root.addLayout(form)
        root.addLayout(buttons)
        root.addLayout(grid, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self.run_button.clicked.connect(self.on_run)
        self.stop_button.clicked.connect(self.on_stop)
        self.bridge.event_received.connect(self.on_event)
        self.bridge.finished.connect(self.on_finished)
        self.bridge.failed.connect(self.on_failed)

        self._load_saved_account()

    # ---------------- 설정 ----------------

    def _load_saved_account(self) -> None:
        try:
            import json

            if self.paths.config_file.exists():
                saved = json.loads(self.paths.config_file.read_text(encoding="utf-8"))
                self.account_input.setText(saved.get("account", ""))
                for panel, kw in zip(self.panels, saved.get("keywords", [])):
                    panel.keyword_input.setText(kw)
                self.exclude_input.setText(", ".join(saved.get("excludes", [])))
        except Exception:
            pass    # 설정 파일이 깨져도 앱은 떠야 한다

    def _save_config(self, config: RunConfig) -> None:
        import json

        self.paths.config_file.write_text(
            json.dumps(
                {
                    "account": config.account,
                    "keywords": config.keywords,
                    "excludes": config.excludes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _collect_raw(self) -> dict:
        return {
            "account": self.account_input.text(),
            "keywords": [p.keyword() for p in self.panels if p.keyword()],
            "excludes": [w.strip() for w in self.exclude_input.text().split(",")],
            "start_date": self.start_date_input.text(),
            "end_date": self.end_date_input.text(),
            "blog_limit": self.blog_limit_input.value(),
            "likes_per_blog": self.likes_input.value(),
            "likes_per_minute": self.rate_input.value(),
            "dry_run": self.dry_run_input.isChecked(),
        }

    # ---------------- 실행 ----------------

    def on_run(self) -> None:
        config, errors = RunConfig.validate(self._collect_raw())
        if errors:
            # 결함 8: 잘못된 필드만 알린다. 나머지를 조용히 되돌리지 않는다.
            QMessageBox.warning(
                self, "설정 오류",
                "\n".join(f"· {e.field}: {e.message}" for e in errors),
            )
            return

        password = self.password_input.text()
        if password:
            keyring.set_password(KEYRING_SERVICE, config.account, password)
            self.password_input.clear()
        stored = keyring.get_password(KEYRING_SERVICE, config.account)
        if not stored:
            QMessageBox.warning(self, "비밀번호 없음",
                                "저장된 비밀번호가 없습니다. 한 번 입력해 주세요.")
            return

        self._save_config(config)
        for panel in self.panels:
            panel.set_alert("")
            panel.set_running(True)
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.summary_label.setText("실행 중…")

        self.bridge.start(lambda emit: self._run_engine(config, stored, emit))

    async def _run_engine(self, config: RunConfig, password: str, emit) -> object:
        history = History(self.paths.history_db)
        session = BrowserSession(self.paths)
        try:
            await session.open(config.account, lambda: password)
        except LoginError:
            history.close()
            await session.close()
            raise

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            async def like_fn(blog_id: str, log_no: str):
                return await press_like(
                    session.page, blog_id, log_no, dry_run=config.dry_run
                )

            self.runner = Runner(
                config=config,
                history=history,
                search=SearchClient(http),
                posts=PostsClient(http),
                like_fn=like_fn,
                limiter=RateLimiter(config.likes_per_minute),
                detector=BlockDetector(),
                emit=emit,
                run_id=uuid.uuid4().hex[:12],
            )
            try:
                return await self.runner.run()
            finally:
                await session.close()
                history.close()

    def on_stop(self) -> None:
        if self.runner is not None:
            self.bridge.request_stop(self.runner.request_stop)
            self.summary_label.setText("정지 요청됨 — 진행 중인 블로그를 마칩니다…")

    # ---------------- 이벤트 ----------------

    def _panel_for(self, keyword: str) -> KeywordPanel | None:
        for panel in self.panels:
            if panel.keyword() == keyword:
                return panel
        return None

    def on_event(self, event) -> None:
        if isinstance(event, WorkerStarted):
            panel = self._panel_for(event.keyword)
            if panel:
                panel.set_status("검색 시작")

        elif isinstance(event, PageCollected):
            panel = self._panel_for(event.keyword)
            if panel:
                total = f"{event.total_count}{'+' if event.total_count >= 1000 else ''}"
                panel.set_status(f"{event.page}페이지 · 총 {total}건")
                panel.append_log(
                    f"{event.page}페이지: {event.found}건 중 {event.queued}건 신규"
                )

        elif isinstance(event, BlogVisited):
            panel = self._panel_for(event.keyword)
            if panel:
                panel.append_log(
                    f"{event.blog_id} 공감 {event.likes_ok}/{event.likes_tried}"
                )

        elif isinstance(event, LikeResultEvent):
            if event.outcome not in ("success", "already_liked"):
                for panel in self.panels:
                    panel.append_log(f"{event.blog_id}/{event.log_no} → {event.outcome}")
                    break

        elif isinstance(event, FallbackUsed):
            # 조용히 잘리는 대신 시끄럽게 알린다 (결함 1 재발 방지).
            for panel in self.panels:
                panel.set_alert(f"폴백 사용: {event.where} — {event.reason}")

        elif isinstance(event, Aborted):
            for panel in self.panels:
                panel.set_alert(f"중단: {event.reason}")

        elif isinstance(event, LogLine):
            panel = self._panel_for(event.keyword) or self.panels[0]
            panel.append_log(event.text)

        elif isinstance(event, RunFinished):
            s = event.summary
            self.summary_label.setText(
                f"블로그 {s.blogs_done} · 공감 {s.likes_ok}/{s.likes_tried} "
                f"· 사유 {s.stop_reason}"
            )

    def on_finished(self, _result) -> None:
        self._reset_controls()

    def on_failed(self, message: str) -> None:
        self._reset_controls()
        self.summary_label.setText("실패")
        QMessageBox.critical(self, "실행 실패", message)

    def _reset_controls(self) -> None:
        for panel in self.panels:
            panel.set_running(False)
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.runner = None


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 전체 테스트 확인**

Run: `python -m pytest -v`
Expected: PASS — 모든 단위 테스트가 통과한다 (계약 테스트는 제외된 상태).

- [ ] **Step 7: 앱 수동 확인**

Run: `python -m desktop.app`
Expected: 창이 뜨고, 창 크기를 바꿔도 레이아웃이 따라 움직이며(절대좌표가 아니므로), 키워드를 비운 채 실행하면 "keywords: 키워드를 하나 이상 입력하세요." 오류만 뜨고 다른 필드는 그대로 남아 있다.

- [ ] **Step 8: 커밋**

```bash
git add desktop/widgets.py desktop/app.py tests/test_widgets.py
git commit -m "feat: add PyQt6 desktop UI with per-keyword panels"
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 태스크 |
|---|---|
| §2 결정 1 (BFS 제거) | 전 태스크 — 공감자 수집 코드가 존재하지 않음 |
| §2 결정 2 (끝까지 순회) | Task 4 `iter_pages` |
| §2 결정 3 (Playwright) | Task 9, 10 |
| §2 결정 4 (키워드 병렬 · 탭 1개) | Task 11 생산자 N + 소비자 1 |
| §2 결정 5 (상한/소진 종료) | Task 11 `budget` · `exhausted` |
| §2 결정 6 (계정별 기록) | Task 6, Task 11 |
| §2 결정 7 (엔진 → 데스크톱) | 태스크 순서 자체 |
| §2 결정 8 (asyncio + 브리지) | Task 11, 13 |
| §3.1 검색 API | Task 3, 4, 12 |
| §3.2 RSS | Task 5, 12 |
| §4 모듈 경계 | File Structure |
| §5 데이터 흐름 | Task 11 |
| §6.1 저장 위치 | Task 1 |
| §6.2 자격증명 (keyring) | Task 14 |
| §6.3 세션 (계정별·암호화) | Task 9 |
| §6.4 방문 기록 | Task 6 |
| §6.5 설정 (필드별 검증) | Task 2, Task 14 |
| §7.1 결과 유형 | Task 10 |
| §7.2 차단 감지 | Task 8 |
| §7.3 속도 제한 | Task 7 |
| §7.4 폴백 알림 | Task 1(`FallbackUsed`), Task 14(`set_alert`) |
| §7.5 오류 전파 경계 | Task 11 `_visit` 반환값 |
| §8 층 1·2·3 | Task 2~11 · Task 12 |

**남은 간극 2건 (의도적, 구현자가 알아야 함):**

- **§7.6 파일 로그(JSON Lines)** — 태스크에 없다. `AppPaths.log_dir`은 Task 1에서 준비되므로, 운영 중 진단이 필요해지면 `emit` 콜백에 파일 기록기를 하나 더 붙이면 된다. 이벤트가 이미 구조화되어 있어 추가 비용이 작다.
- **`FallbackUsed`를 실제로 방출하는 지점** — `search.py`의 DOM 폴백 자체를 구현하지 않았다. JSON 파싱이 실패하면 `SearchParseError`가 올라와 `LogLine`으로 표시되고 계약 테스트(Task 12)가 원인을 알려준다. **조용히 잘리지 않는다는 목표는 달성되며**, DOM 폴백은 실제로 JSON이 깨졌을 때 그 형태를 보고 만드는 편이 정확하다. 지금 추측으로 만든 폴백은 검증할 방법이 없다.

**2. 플레이스홀더 스캔:** TBD/TODO 없음. 모든 코드 단계에 실제 코드가 있다.

**3. 타입 일관성 확인 완료:**
- `LikeOutcome` — Task 3에서 `models.py`에 정의, Task 8·10·11에서 동일 이름 사용
- `Target(blog_id, keyword, seed_log_no)` — Task 3 정의, Task 11에서 위치 인자로 생성
- `History.was_visited(account, blog_id)` — Task 6 정의, Task 11에서 계정 전달
- `RunSummary` — Task 1 `events.py` 정의, Task 11에서 생성, Task 14에서 필드 참조
- `RateLimiter(per_minute, *, clock, sleeper, rng)` — Task 7 정의, Task 11 테스트와 Task 14에서 동일 시그니처
- `press_like(page, blog_id, log_no, *, dry_run)` — Task 10 정의, Task 12·14에서 동일 호출
- `SearchClient.iter_pages(query, start_date, end_date, first_page=1)` — Task 4 정의, Task 11의 `FakeSearch`가 같은 시그니처를 흉내냄

---

## 실행 순서 요약

```
Task 1  스캐폴딩 · paths · events · 픽스처
Task 2  config — 필드별 검증
Task 3  검색 파서 (순수)
Task 4  검색 페이지네이션 (HTTP)
Task 5  posts — RSS
Task 6  history — 계정별 SQLite
Task 7  ratelimit
Task 8  safety — 차단 감지
Task 9  session — 브라우저 · 로그인
Task 10 like — 공감 클릭
Task 11 runner — 오케스트레이션        ← 여기까지가 엔진, 단독으로 동작·검증 가능
Task 12 계약 테스트 + 드라이런          ← 실물 셀렉터 확정
Task 13 bridge
Task 14 데스크톱 UI
```

Task 11까지 마치면 엔진이 완성되어 단위 테스트로 전부 검증된다. Task 12의 드라이런에서 실물 셀렉터를 확정한 뒤 UI를 얹는 순서다.
