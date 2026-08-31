"""키워드 패널 — 키워드 하나의 입력 · 상태 · 로그를 담는다.

레거시는 절대좌표(1323x890 고정)로 배치해 DPI와 해상도에 취약했고, 4개
패널이 상단의 같은 키워드를 공유해 사실상 같은 작업을 4번 돌렸다. 여기서는
패널마다 자기 키워드를 갖고 레이아웃 매니저로 배치한다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

MAX_LOG_BLOCKS = 2000

# 앱 안의 경고색은 하나로 둔다 — 경고 배너와 "지금 멈출 수 있다"는 정지 버튼이
# 같은 빨강을 쓴다.
ALERT_RED = "#b00020"
ALERT_STYLE = f"background: {ALERT_RED}; color: white; padding: 4px; border-radius: 3px;"
STOP_RUNNING_STYLE = f"background: {ALERT_RED}; color: white; padding: 4px 10px; border-radius: 3px;"


class KeywordPanel(QGroupBox):
    def __init__(self, index: int) -> None:
        super().__init__(f"키워드 {index}")
        self.index = index

        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("예: 교대 헬스장")

        self.start_button = QPushButton("▶ 실행")
        self.stop_button = QPushButton("■ 정지")
        self.stop_button.setEnabled(False)

        self.status_label = QLabel("대기 중")
        self.alert_label = QLabel("")
        self.alert_label.setWordWrap(True)
        self.alert_label.setStyleSheet(ALERT_STYLE)
        self.alert_label.hide()

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(MAX_LOG_BLOCKS)

        controls = QHBoxLayout()
        controls.addWidget(self.keyword_input, 1)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addWidget(self.alert_label)
        layout.addWidget(self.log_view, 1)

    def keyword(self) -> str:
        return self.keyword_input.text().strip()

    def set_running(self, running: bool, *, participating: bool = True) -> None:
        """participating=False는 "다른 키워드로 실행 중"이라는 뜻이다.

        실행은 계정 단위로 하나이므로(결정 4) 어떤 실행이 도는 동안에는 모든
        패널의 ▶가 잠긴다. 반면 ■는 그 실행에 참여한 패널에서만 살아 있다 —
        참여하지 않은 패널에는 멈출 것이 없다.
        """
        can_stop = running and participating
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(can_stop)
        self.keyword_input.setEnabled(not running)
        self.stop_button.setStyleSheet(STOP_RUNNING_STYLE if can_stop else "")

    def append_log(self, text: str) -> None:
        self.log_view.append(text)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_alert(self, text: str) -> None:
        self.alert_label.setText(text)
        self.alert_label.setVisible(bool(text))
