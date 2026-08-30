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


def test_cancelled_error_surfaces_as_failed_not_dropped(app):
    """Runner.run() re-raises CancelledError after recording its summary (an
    earlier ruling), so this BaseException subclass is the terminal exception
    most likely to reach the bridge in practice. It must still produce a
    terminal signal — not vanish, leaving the UI stuck "running" forever."""
    errors = []
    done = []
    bridge = EngineBridge()
    bridge.failed.connect(errors.append)
    bridge.finished.connect(done.append)

    async def cancelled(emit):
        raise asyncio.CancelledError()

    bridge.start(cancelled)
    assert _pump(lambda: errors)
    assert len(errors) == 1
    assert done == []
    assert bridge.is_running() is False


def test_request_stop_does_not_raise_when_loop_closes_underneath_it(app):
    """Simulates the race where the worker's run_until_complete returns (and
    the loop stops/closes) in the gap between request_stop's is_running()
    check and its call_soon_threadsafe call. Without the fix this raises
    RuntimeError on the Qt (calling) thread, which PyQt6 would otherwise let
    kill the app from inside a slot."""

    class RaceyLoop:
        def is_running(self):
            return True

        def call_soon_threadsafe(self, callback):
            raise RuntimeError("Event loop is closed")

    bridge = EngineBridge()
    bridge._loop = RaceyLoop()

    bridge.request_stop(lambda: None)  # must not raise


def test_request_stop_after_completion_does_not_raise(app):
    bridge = EngineBridge()
    done = []
    bridge.finished.connect(done.append)

    async def work(emit):
        return None

    bridge.start(work)
    assert _pump(lambda: done)

    bridge.request_stop(lambda: None)  # loop is gone; must not raise
