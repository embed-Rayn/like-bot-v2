"""desktop/firstrun.py — 최초 실행 시 chromium 내려받기 화면.

QThread를 start()하지 않고 run()을 직접 부른다. 그러면 시그널이 테스트 스레드
안에서 동기적으로 오므로 이벤트 루프 없이 검증할 수 있다.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from engine.browsers import BrowserInstallError


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


class FakeProcess:
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


# ---------------- ensure_chromium ----------------


def test_installed_browser_skips_the_dialog_entirely(app):
    from desktop.firstrun import ensure_chromium

    def no_worker():
        raise AssertionError("설치돼 있는데 내려받기를 시작했다")

    assert ensure_chromium(installed=lambda: True, make_worker=no_worker) is True


# ---------------- InstallWorker ----------------


def test_worker_forwards_progress_lines(app):
    from desktop.firstrun import InstallWorker

    def fake_install(on_progress, on_spawn):
        on_spawn(FakeProcess())
        on_progress("Downloading Chromium 1234")
        on_progress("done")

    worker = InstallWorker(install=fake_install)
    seen = []
    worker.progress.connect(seen.append)
    worker.run()

    assert seen == ["Downloading Chromium 1234", "done"]


def test_worker_reports_the_failure_text(app):
    from desktop.firstrun import InstallWorker

    def fake_install(on_progress, on_spawn):
        on_spawn(FakeProcess())
        raise BrowserInstallError("종료코드 1\nENOTFOUND")

    worker = InstallWorker(install=fake_install)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors and "ENOTFOUND" in errors[0]


def test_cancel_kills_the_download_and_stays_quiet(app):
    """취소는 실패가 아니다 — 죽인 프로세스가 낸 오류로 경고창을 띄우면 안 된다."""
    from desktop.firstrun import InstallWorker

    process = FakeProcess()

    def fake_install(on_progress, on_spawn):
        on_spawn(process)
        worker.cancel()
        raise BrowserInstallError("종료코드 1\nkilled")

    worker = InstallWorker(install=fake_install)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert process.killed is True
    assert errors == []


def test_cancel_before_the_process_exists_still_kills_it(app):
    """취소를 눌렀는데 node가 그 직후에 떴다면, 뜨자마자 죽여야 한다."""
    from desktop.firstrun import InstallWorker

    process = FakeProcess()
    worker = InstallWorker(install=lambda on_progress, on_spawn: on_spawn(process))
    worker.cancel()
    worker.run()

    assert process.killed is True


def test_unexpected_errors_are_reported_too(app):
    """BrowserInstallError만 잡으면 드라이버 누락 같은 예외가 워커에서 조용히
    사라지고, 대화상자만 남아 영원히 돌아간다."""
    from desktop.firstrun import InstallWorker

    def fake_install(on_progress, on_spawn):
        raise FileNotFoundError("node.exe")

    worker = InstallWorker(install=fake_install)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors and "node.exe" in errors[0]
