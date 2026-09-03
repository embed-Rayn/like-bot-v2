"""최초 실행 — chromium이 없으면 내려받는다.

chromium은 exe에 넣지 않는다(engine/browsers.py 참고). 그래서 새 PC에서 처음
켜면 브라우저가 없고, 그대로 두면 아이디와 비밀번호를 다 넣고 실행을 누른
뒤에야 session.open()에서 정체 모를 예외로 터진다. 창을 띄우기 전에 여기서
확인하고, 없으면 진행 상황을 보여주며 받아 온다.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from engine.browsers import chromium_installed, install_chromium

TITLE = "브라우저 준비"
MESSAGE = "공감을 누를 브라우저(chromium)를 처음 한 번 내려받습니다 · 약 150MB"

Install = Callable[[Callable[[str], None], Callable[[subprocess.Popen], None]], None]


class InstallWorker(QThread):
    """내려받기를 별도 스레드에서 돌린다 — UI 스레드가 몇 분을 멈추면 안 된다."""

    progress = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        install: Install = install_chromium,
    ) -> None:
        super().__init__(parent)
        self._install = install
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def run(self) -> None:
        try:
            self._install(self.progress.emit, self._remember)
        except Exception as error:
            # 취소하면 죽인 프로세스가 0이 아닌 종료코드를 내므로 예외가 온다.
            # 그것은 실패가 아니다 — 운영자가 스스로 멈춘 것이다.
            if not self._cancelled:
                # BrowserInstallError만 잡으면 드라이버 누락 같은 예외가 여기서
                # 조용히 사라지고, 대화상자는 끝나지 않은 채로 남는다.
                self.failed.emit(str(error))

    def _remember(self, process: subprocess.Popen) -> None:
        self._process = process
        # cancel()과 프로세스 기동 사이의 짧은 틈. 이미 취소됐다면 뜨자마자 죽인다.
        if self._cancelled:
            process.kill()

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None:
            self._process.kill()


def ensure_chromium(
    parent: QWidget | None = None,
    *,
    installed: Callable[[], bool] = chromium_installed,
    make_worker: Callable[[], InstallWorker] = InstallWorker,
) -> bool:
    """chromium을 쓸 수 있으면 True. 창을 띄우기 전에 부른다."""
    if installed():
        return True

    dialog = QProgressDialog(MESSAGE, "취소", 0, 0, parent)  # 0,0 = 진행률 미정
    dialog.setWindowTitle(TITLE)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setMinimumWidth(520)
    # 기본값은 4초 뒤 자동 표시 + 취소 시 자동 reset이다. 둘 다 여기서는
    # 방해가 된다 — 처음부터 보여야 하고, 닫는 시점은 워커가 정한다.
    dialog.setMinimumDuration(0)
    dialog.setAutoReset(False)
    dialog.setAutoClose(False)

    worker = make_worker()
    errors: list[str] = []
    worker.progress.connect(lambda line: dialog.setLabelText(f"{MESSAGE}\n\n{line}"))
    worker.failed.connect(errors.append)
    worker.finished.connect(dialog.close)
    dialog.canceled.connect(worker.cancel)

    worker.start()
    dialog.exec()
    worker.wait()

    if errors:
        QMessageBox.critical(parent, "브라우저 설치 실패", errors[0])
        return False
    # 취소했으면 여전히 없다. 성공/취소를 따로 세지 않고 사실을 다시 확인한다.
    return installed()
