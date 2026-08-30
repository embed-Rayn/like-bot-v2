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
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def start(self, coro_factory: CoroFactory) -> None:
        if self.is_running():
            raise RuntimeError("이미 실행 중입니다.")

        self._running = True

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            result: object = None
            error: str | None = None
            try:
                try:
                    result = loop.run_until_complete(coro_factory(self.event_received.emit))
                except Exception:
                    error = traceback.format_exc(limit=5)
            finally:
                loop.close()
                if self._loop is loop:
                    self._loop = None
                self._running = False

            if error is None:
                self.finished.emit(result)
            else:
                self.failed.emit(error)

        self._thread = threading.Thread(target=run, name="engine", daemon=True)
        self._thread.start()

    def request_stop(self, callback: Callable[[], None]) -> None:
        """엔진 루프 스레드 안에서 callback을 실행한다 (예: Runner.request_stop)."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(callback)
