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
        # CRITICAL 2: self.runner는 session.open()(브라우저 기동 · 로그인 ·
        # 캡차 대기까지 포함)이 끝난 뒤에야 생긴다. 그 창에서 정지를 눌러도
        # on_stop이 할 일이 없어 보이면 안 된다 — 이 플래그가 요청 자체를
        # 기억해 두고, _run_engine이 session.open()에서 돌아오는 즉시 확인한다.
        self._stop_requested = False

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
                # I5: 스펙 §6.5는 config.json에 키워드 · 기간 · 방문 상한 ·
                # 블로그당 공감 수 · 속도 제한 · 제외 단어를 담으라고 명시한다.
                # 특히 속도 제한은 §7.3에 따라 운영하며 조정해야 하는 값이라,
                # 저장하지 않으면 조심스럽게 낮춰 둔 값이 실행할 때마다
                # 기본값(6.0)으로 조용히 되돌아가고 다음 실행이 의도보다
                # 빨리 돈다. .get()의 기본값은 폼 위젯이 이미 들고 있는
                # 기본값과 같으므로, 키가 없어도(예: 옛 config.json) 조용히
                # 지금 상태를 유지한다.
                if "start_date" in saved:
                    self.start_date_input.setText(str(saved["start_date"]))
                if "end_date" in saved:
                    self.end_date_input.setText(str(saved["end_date"]))
                if "blog_limit" in saved:
                    self.blog_limit_input.setValue(int(saved["blog_limit"]))
                if "likes_per_blog" in saved:
                    self.likes_input.setValue(int(saved["likes_per_blog"]))
                if "likes_per_minute" in saved:
                    self.rate_input.setValue(float(saved["likes_per_minute"]))
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
                    "start_date": config.start_date,
                    "end_date": config.end_date,
                    "blog_limit": config.blog_limit,
                    "likes_per_blog": config.likes_per_blog,
                    "likes_per_minute": config.likes_per_minute,
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
        self._stop_requested = False
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

        # CRITICAL 2: session.open()이 캡차 · 로그인 대기로 오래 걸리는
        # 동안 정지가 눌렸을 수 있다. self.runner는 아직 없으므로 그 요청은
        # 이 플래그에만 남아 있다 — Runner를 만들기 전에 여기서 확인한다.
        # 그러지 않으면 이미 정지를 요청한 운영자 앞에서 실행이 그대로
        # 시작되고, 창은 (버그 수정 전처럼) 실행이 끝날 때까지 닫히지 않는다.
        if self._stop_requested:
            await session.close()
            history.close()
            return None

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
        # CRITICAL 2: self.runner가 아직 없어도(브라우저 기동 · 로그인 대기
        # 중) 요청은 항상 기억해 두고 화면에 반영한다. 예전에는 여기서 아무
        # 일도 하지 않아 ■ 버튼이 죽은 것처럼 보였다.
        self._stop_requested = True
        if self.runner is not None:
            self.bridge.request_stop(self.runner.request_stop)
            self.summary_label.setText("정지 요청됨 — 진행 중인 블로그를 마칩니다…")
        else:
            self.summary_label.setText("정지 요청됨 — 로그인/준비 중입니다…")

    # ---------------- 종료 ----------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        if not self.bridge.is_running():
            event.accept()
            return

        if self._close_pending:
            # 이미 정지를 요청하고 기다리는 중이다 — 다시 묻지는 않지만,
            # 정지 요청 자체는 다시 보낸다. 그러지 않으면 첫 요청이 유실된
            # 경우(예: 브리지 루프가 아직 뜨기 전) 창을 다시 닫으려는 모든
            # 시도가 이 분기에서 그냥 무시되어 창이 영원히 닫히지 않는다.
            self.on_stop()
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
            # I3: 드라이런 결과는 실제 공감을 누른 것이 아니므로, 요약 라벨이
            # 진짜 실행과 글자 하나까지 같으면 안 된다 — runs 테이블은 이제
            # dry_run 컬럼으로 구분되지만, 화면도 그래야 운영자가 착각하지
            # 않는다.
            prefix = "[드라이런] " if s.dry_run else ""
            self.summary_label.setText(
                f"{prefix}블로그 {s.blogs_done} · 공감 {s.likes_ok}/{s.likes_tried} "
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
