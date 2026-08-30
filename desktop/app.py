"""메인 창 — 설정 입력, 실행/정지, 키워드별 로그.

키워드 패널마다 자기 키워드를 갖지만, 실행은 계정 단위로 하나다. 공감이
계정 단위 속도 제한에 묶여 있어 여러 실행을 동시에 돌릴 이유가 없다 (결정 4).
"""
from __future__ import annotations

import sys
import uuid

import httpx
import keyring
from PyQt6.QtGui import QCloseEvent
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

# R17: stop_reason은 엔진 내부 값 그대로 두면 운영자가 알아볼 수 없다
# (예: "not_logged_in"). 화면에는 짧은 한글 문구로 바꿔 보여준다. 목록에
# 없는 값(향후 추가되거나 오탈자)은 원문 그대로 보여줘 예외를 내지 않는다.
STOP_REASON_KO: dict[str, str] = {
    "budget": "방문 상한 도달",
    "exhausted": "검색 결과 소진",
    "user": "사용자 중지",
    "blocked": "차단 의심 — 중단",
    "not_logged_in": "로그인 풀림 — 중단",
    "error": "오류",
}


def format_stop_reason(reason: str) -> str:
    return STOP_REASON_KO.get(reason, reason)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("blog search & like")
        self.resize(1280, 860)

        self.paths = AppPaths.for_app()
        self.paths.ensure()
        self.bridge = EngineBridge()
        self.runner: Runner | None = None
        # R16: 창을 닫아도 데몬 스레드가 즉시 죽어서는 안 된다 — 실행 중이면
        # 먼저 정지를 요청하고, closeEvent는 event.ignore()로 창을 열어둔
        # 채 finished/failed가 실제로 올 때까지 기다린다. 그래야 _run_engine의
        # finally가 브라우저 세션을 정상적으로 닫는다.
        self._close_pending = False

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

        # dry_run은 여기 담지 않는다: 다음 실행에서 체크박스 상태를 그대로
        # 물려받으면(특히 문자열로 왕복하면 "False"가 truthy가 되는 함정이
        # 있다) 운영자가 다시 확인하지 않은 채 실제 실행이 되어버릴 수 있다.
        # 매번 체크박스에서 실제 bool을 읽는다.
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

    # ---------------- 종료 ----------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if not self.bridge.is_running():
            event.accept()
            return

        if self._close_pending:
            # 이미 정지를 요청하고 기다리는 중이다 — 다시 묻지 않는다.
            event.ignore()
            return

        reply = QMessageBox.question(
            self,
            "실행 중",
            "아직 실행 중입니다. 정지하고 창을 닫을까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        self._close_pending = True
        self.on_stop()  # ■ 버튼과 같은 경로 — 브리지를 거쳐 정지를 요청한다.
        self.summary_label.setText("종료 중 — 정지를 기다리는 중…")
        event.ignore()

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
                f"· 사유 {format_stop_reason(s.stop_reason)}"
            )

    def on_finished(self, _result) -> None:
        self._reset_controls()
        if self._close_pending:
            self.close()

    def on_failed(self, message: str) -> None:
        self._reset_controls()
        if self._close_pending:
            self.close()
            return
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
