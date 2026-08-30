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
