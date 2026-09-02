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
    for path in (p.config_file, p.history_db, p.sessions_dir, p.log_dir, p.browsers_dir):
        assert tmp_path in path.parents or path.parent == tmp_path
