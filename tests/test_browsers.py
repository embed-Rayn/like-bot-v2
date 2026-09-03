"""engine.browsers — chromium 설치 확인과 최초 내려받기.

네트워크도 node도 건드리지 않는다. 실제 다운로드는 층 3(browser)의 몫이고,
여기서는 명령을 어떻게 만들고 출력을 어떻게 흘리고 실패를 어떻게 알리는지만
본다.
"""
import subprocess
import sys

import pytest

from engine import browsers


class FakeProcess:
    """Popen 중 install_chromium이 실제로 쓰는 부분만 흉내 낸다."""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._returncode = returncode
        self.killed = False

    def wait(self):
        return self._returncode

    def kill(self):
        self.killed = True


def test_chromium_installed_follows_the_executable_path(monkeypatch, tmp_path):
    exe = tmp_path / "chrome.exe"
    monkeypatch.setattr(browsers, "chromium_executable", lambda: exe)
    assert browsers.chromium_installed() is False
    exe.write_bytes(b"")
    assert browsers.chromium_installed() is True


def test_chromium_installed_is_false_when_playwright_cannot_answer(monkeypatch):
    """드라이버 기동 자체가 실패해도 앱이 죽어서는 안 된다 — 설치하러 간다."""
    def boom():
        raise RuntimeError("driver missing")

    monkeypatch.setattr(browsers, "chromium_executable", boom)
    assert browsers.chromium_installed() is False


def test_install_command_runs_the_driver_cli(monkeypatch):
    monkeypatch.setattr(
        browsers, "_driver_paths", lambda: ("/d/node.exe", "/d/package/cli.js")
    )
    assert browsers.install_command() == [
        "/d/node.exe", "/d/package/cli.js", "install", "chromium"
    ]


def test_install_chromium_streams_every_line_stripped():
    seen = []
    process = FakeProcess(["Downloading Chromium 1234\n", "|####| 100%\n"])

    browsers.install_chromium(seen.append, popen=lambda: process)

    assert seen == ["Downloading Chromium 1234", "|####| 100%"]


def test_install_chromium_raises_on_nonzero_exit():
    process = FakeProcess(["first\n", "boom: ENOTFOUND\n"], returncode=1)

    with pytest.raises(browsers.BrowserInstallError) as excinfo:
        browsers.install_chromium(popen=lambda: process)

    # 마지막 줄이 원인이다. 종료코드만 던지면 운영자가 볼 것이 없다.
    message = str(excinfo.value)
    assert "boom: ENOTFOUND" in message
    assert "1" in message


def test_error_message_keeps_only_the_tail():
    """진행바가 수백 줄이라 전부 붙이면 원인이 파묻힌다."""
    noise = [f"line {i}\n" for i in range(200)]
    process = FakeProcess(noise, returncode=1)

    with pytest.raises(browsers.BrowserInstallError) as excinfo:
        browsers.install_chromium(popen=lambda: process)

    message = str(excinfo.value)
    assert "line 199" in message
    assert "line 100" not in message


def test_install_chromium_works_without_a_callback():
    browsers.install_chromium(popen=lambda: FakeProcess([]))


def test_on_spawn_receives_the_process_so_a_cancel_can_kill_it():
    process = FakeProcess(["a\n"])
    handed = []

    browsers.install_chromium(on_spawn=handed.append, popen=lambda: process)

    assert handed == [process]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용 플래그")
def test_subprocess_hides_the_console_window():
    """--windowed exe에서 node를 띄우면 콘솔 창이 번쩍인다. 꺼야 한다."""
    assert browsers._creation_flags() == subprocess.CREATE_NO_WINDOW


# ---------------- 진단 로그 ----------------


def test_debug_logging_is_off_by_default(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv(browsers.DEBUG_ENV, raising=False)
    monkeypatch.setattr(browsers, "chromium_executable", lambda: tmp_path / "x.exe")

    browsers.chromium_installed()

    assert capsys.readouterr().err == ""


def test_debug_logging_reports_the_resolved_path(monkeypatch, capsys, tmp_path):
    """브라우저를 못 찾는다는 신고가 들어오면 알아야 할 것은 딱 두 가지다 —
    어느 경로를 봤는가, 거기 있었는가."""
    monkeypatch.setenv(browsers.DEBUG_ENV, "1")
    exe = tmp_path / "chrome.exe"
    monkeypatch.setattr(browsers, "chromium_executable", lambda: exe)

    browsers.chromium_installed()

    err = capsys.readouterr().err
    assert str(exe) in err
    assert "False" in err


def test_debug_logging_reports_the_failure_reason(monkeypatch, capsys):
    monkeypatch.setenv(browsers.DEBUG_ENV, "1")

    def boom():
        raise RuntimeError("driver missing")

    monkeypatch.setattr(browsers, "chromium_executable", boom)

    assert browsers.chromium_installed() is False
    assert "driver missing" in capsys.readouterr().err
