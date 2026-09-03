import json

import pytest

from engine.config import RunConfig
from engine.events import BlogVisited, LogLine, RunFinished, RunSummary
from engine.paths import AppPaths
from engine.runlog import RunLog, open_run_log, prune


def make_config(**over) -> RunConfig:
    fields = dict(
        account="myblog",
        keywords=["다이어트 식단", "홈트"],
        excludes=["협찬"],
        start_date="2026-09-01",
        end_date="2026-09-03",
        blog_limit=30,
        likes_per_blog=2,
        likes_per_minute=6.0,
        dry_run=False,
    )
    fields.update(over)
    return RunConfig(**fields)


def read_lines(path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_file_is_named_for_the_run(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "ab12cd34ef56", make_config()) as log:
        target = log.path
    assert target.parent == paths.log_dir
    assert target.name.startswith("run-")
    assert target.name.endswith("-ab12cd34ef56.jsonl")


def test_header_line_records_what_the_run_was(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "ab12cd34ef56", make_config()) as log:
        pass
    header = read_lines(log.path)[0]
    assert header["type"] == "RunStarted"
    assert header["run_id"] == "ab12cd34ef56"
    assert header["account"] == "myblog"
    assert header["keywords"] == ["다이어트 식단", "홈트"]
    assert header["dry_run"] is False
    assert header["blog_limit"] == 30
    assert header["likes_per_blog"] == 2
    assert header["likes_per_minute"] == 6.0
    assert header["start_date"] == "2026-09-01"
    assert header["end_date"] == "2026-09-03"
    assert header["t"]


def test_header_never_carries_a_password_or_session(tmp_path):
    """헤더는 config에서 필드를 하나씩 골라 담는다 — asdict로 통째로 담지 않는다.

    config에 나중에 민감한 필드가 붙어도 조용히 파일로 새지 않게 하기 위한 것이다.
    """
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "run1", make_config()) as log:
        pass
    header = read_lines(log.path)[0]
    assert set(header) == {
        "t", "type", "run_id", "account", "keywords", "excludes",
        "start_date", "end_date", "blog_limit", "likes_per_blog",
        "likes_per_minute", "dry_run",
    }


def test_each_event_is_one_line(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "run1", make_config()) as log:
        log.write(BlogVisited("홈트", "blog_a", 2, 2))
        log.write(BlogVisited("홈트", "blog_b", 1, 2))
    lines = read_lines(log.path)
    assert len(lines) == 3  # 헤더 + 이벤트 2건
    assert [line["blog_id"] for line in lines[1:]] == ["blog_a", "blog_b"]
    assert lines[1]["type"] == "BlogVisited"
    assert lines[1]["likes_ok"] == 2
    assert lines[1]["t"]


def test_nested_summary_is_serialized(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    summary = RunSummary(
        run_id="run1", blogs_done=4, likes_ok=7, likes_tried=8,
        stop_reason="budget", per_keyword={"홈트": 4}, dry_run=True,
    )
    with open_run_log(paths, "run1", make_config()) as log:
        log.write(RunFinished(summary))
    line = read_lines(log.path)[-1]
    assert line["type"] == "RunFinished"
    assert line["summary"]["stop_reason"] == "budget"
    assert line["summary"]["per_keyword"] == {"홈트": 4}


def test_korean_stays_readable(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "run1", make_config()) as log:
        log.write(LogLine("홈트", "공감 없음"))
    raw = log.path.read_text(encoding="utf-8")
    assert "공감 없음" in raw
    assert "\\u" not in raw


def test_writing_after_a_failure_never_raises(tmp_path):
    """기록이 실행을 죽이면 안 된다 — 로그를 남기려다 공감을 못 누르는 건 본말전도다."""
    paths = AppPaths.for_app(tmp_path)
    log = open_run_log(paths, "run1", make_config())
    log._handle.close()          # 디스크 가득참 · 파일 잠김을 흉내낸다
    log.write(LogLine("", "이 줄은 버려진다"))
    log.write(LogLine("", "그 뒤로도 조용하다"))
    log.close()
    assert log.failed


def test_close_is_idempotent(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    log = open_run_log(paths, "run1", make_config())
    log.close()
    log.close()


def test_open_creates_the_log_directory(tmp_path):
    paths = AppPaths.for_app(tmp_path / "nothing-here")
    with open_run_log(paths, "run1", make_config()) as log:
        assert log.path.parent.is_dir()


def test_prune_keeps_the_newest_runs(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    made = []
    for i in range(5):
        f = log_dir / f"run-2026090{i}-000000-run{i}.jsonl"
        f.write_text("{}\n", encoding="utf-8")
        made.append(f)
    prune(log_dir, keep=2)
    left = sorted(p.name for p in log_dir.glob("run-*.jsonl"))
    assert left == [made[3].name, made[4].name]


def test_prune_leaves_other_files_alone(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    crash = log_dir / "crash.log"
    crash.write_text("트레이스백", encoding="utf-8")
    for i in range(3):
        (log_dir / f"run-2026090{i}-000000-run{i}.jsonl").write_text("{}\n", encoding="utf-8")
    prune(log_dir, keep=1)
    assert crash.exists()
    assert len(list(log_dir.glob("run-*.jsonl"))) == 1


def test_prune_on_a_missing_directory_is_quiet(tmp_path):
    prune(tmp_path / "nope", keep=3)


def test_open_prunes_old_runs(tmp_path):
    paths = AppPaths.for_app(tmp_path)
    paths.ensure()
    for i in range(4):
        (paths.log_dir / f"run-2026090{i}-000000-old{i}.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
    with open_run_log(paths, "new1", make_config(), keep=2) as log:
        pass
    names = sorted(p.name for p in paths.log_dir.glob("run-*.jsonl"))
    assert log.path.name in names
    assert len(names) == 2


@pytest.mark.parametrize("bad_id", ["a/b", "..", "c:d"])
def test_run_id_cannot_escape_the_log_directory(tmp_path, bad_id):
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, bad_id, make_config()) as log:
        assert log.path.parent == paths.log_dir


def test_a_failed_open_still_gives_a_usable_object(tmp_path):
    """로그 파일을 못 여는 상황에서도 실행은 계속돼야 한다."""
    blocker = tmp_path / "logs"
    blocker.write_text("이 자리는 파일이라 디렉터리를 만들 수 없다", encoding="utf-8")
    paths = AppPaths.for_app(tmp_path)
    with open_run_log(paths, "run1", make_config()) as log:
        log.write(LogLine("", "조용히 버려진다"))
    assert log.failed


def test_run_log_is_a_no_op_when_disabled(tmp_path):
    """RunLog.disabled()는 파일을 하나도 만들지 않는다."""
    log = RunLog.disabled()
    log.write(LogLine("", "아무 데도 안 간다"))
    log.close()
    assert log.failed
    assert log.path is None
    assert not list(tmp_path.iterdir())
