"""배포본이 chromium을 어디서 찾고 어떻게 받아오는지.

가짜 서브프로세스를 쓰지 않는다 — install_chromium이 실제로 검증해야 하는 것은
"자식 프로세스의 출력을 한 줄씩 흘리고 실패를 예외로 올린다"이고, 그건 진짜
프로세스를 하나 띄워야만 증명된다. 명령만 주입한다.
"""
import sys

import pytest

from engine.browsers import (
    BrowserInstallError,
    apply_browsers_env,
    chromium_present,
    driver_install_command,
    ensure_chromium,
    install_chromium,
)
from engine.paths import AppPaths

ENV = "PLAYWRIGHT_BROWSERS_PATH"


def test_dev_run_leaves_the_environment_alone(tmp_path, monkeypatch):
    """개발 실행은 이미 받아둔 캐시를 그대로 써야 한다."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(ENV, raising=False)

    apply_browsers_env(AppPaths.for_app(tmp_path))

    assert ENV not in __import__("os").environ


def test_frozen_run_points_at_the_app_folder(tmp_path, monkeypatch):
    """배포본은 개발 PC의 ms-playwright 캐시와 섞이면 안 된다."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(ENV, raising=False)
    paths = AppPaths.for_app(tmp_path)

    apply_browsers_env(paths)

    assert __import__("os").environ[ENV] == str(paths.browsers_dir)


def test_an_explicit_environment_setting_wins(tmp_path, monkeypatch):
    """운영자가 직접 지정한 경로를 배포본이 덮어쓰면 진단이 불가능해진다."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(ENV, "D:/elsewhere")

    apply_browsers_env(AppPaths.for_app(tmp_path))

    assert __import__("os").environ[ENV] == "D:/elsewhere"


def test_chromium_present_is_false_when_nothing_was_downloaded(tmp_path):
    assert chromium_present(tmp_path) is False


def test_chromium_present_is_false_for_a_missing_folder(tmp_path):
    assert chromium_present(tmp_path / "nope") is False


def test_chromium_present_finds_a_versioned_folder(tmp_path):
    (tmp_path / "chromium-1234").mkdir()
    assert chromium_present(tmp_path) is True


def test_chromium_present_ignores_the_headless_shell(tmp_path):
    """헤드리스 셸만 있으면 창을 띄울 수 없다 — 로그인이 불가능하다."""
    (tmp_path / "chromium_headless_shell-1234").mkdir()
    assert chromium_present(tmp_path) is False


def test_driver_install_command_ends_with_install_chromium():
    command = driver_install_command()
    assert command[-2:] == ["install", "chromium"]


async def test_install_streams_every_output_line():
    lines: list[str] = []
    await install_chromium(
        lines.append,
        command=[sys.executable, "-c", "print('Downloading'); print('Done')"],
    )
    assert lines == ["Downloading", "Done"]


async def test_install_reports_stderr_too():
    """playwright 드라이버는 진행 상황을 stderr로도 쓴다."""
    lines: list[str] = []
    await install_chromium(
        lines.append,
        command=[sys.executable, "-c", "import sys; print('oops', file=sys.stderr)"],
    )
    assert lines == ["oops"]


async def test_install_failure_raises():
    with pytest.raises(BrowserInstallError):
        await install_chromium(
            lambda line: None,
            command=[sys.executable, "-c", "raise SystemExit(3)"],
        )


# ---------------- 실행 직전 브라우저 확보 ----------------


async def test_ensure_does_nothing_when_no_managed_folder_is_set(monkeypatch):
    """개발 실행은 playwright 기본 캐시를 쓴다 — 그 경로를 우리가 추측하지 않는다."""
    monkeypatch.delenv(ENV, raising=False)
    called = []

    await ensure_chromium(lambda line: None, installer=lambda on_line: called.append(1))

    assert called == []


async def test_ensure_does_not_reinstall_what_is_already_there(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path))
    (tmp_path / "chromium-1234").mkdir()
    called = []

    await ensure_chromium(lambda line: None, installer=lambda on_line: called.append(1))

    assert called == []


async def test_ensure_installs_into_an_empty_managed_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path))
    lines: list[str] = []

    async def fake_installer(on_line):
        on_line("Downloading Chromium")

    await ensure_chromium(lines.append, installer=fake_installer)

    assert "Downloading Chromium" in lines
    assert lines[0] != "Downloading Chromium", "설치 전에 무슨 일이 일어나는지 먼저 알려야 한다"


# ---------------- chromium을 함께 실은 배포본 ----------------


def test_bundled_browsers_win_over_the_app_folder(tmp_path, monkeypatch):
    """exe와 함께 chromium을 실어 보냈으면 그걸 쓴다 — 또 받을 이유가 없다."""
    bundle = tmp_path / "bundle"
    (bundle / "ms-playwright" / "chromium-1234").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.delenv(ENV, raising=False)

    apply_browsers_env(AppPaths.for_app(tmp_path / "appdata"))

    assert __import__("os").environ[ENV] == str(bundle / "ms-playwright")


def test_an_empty_bundle_folder_falls_back_to_the_app_folder(tmp_path, monkeypatch):
    """폴더만 있고 브라우저가 없으면 실어 보내지 않은 것과 같다 — 받아야 한다."""
    bundle = tmp_path / "bundle"
    (bundle / "ms-playwright").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.delenv(ENV, raising=False)
    paths = AppPaths.for_app(tmp_path / "appdata")

    apply_browsers_env(paths)

    assert __import__("os").environ[ENV] == str(paths.browsers_dir)


def test_a_build_without_bundled_browsers_still_uses_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.delenv(ENV, raising=False)
    paths = AppPaths.for_app(tmp_path / "appdata")

    apply_browsers_env(paths)

    assert __import__("os").environ[ENV] == str(paths.browsers_dir)
