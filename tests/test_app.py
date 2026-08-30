"""desktop/app.py는 대부분 배선(wiring)이라 단위 테스트로 다루기 어렵다.

여기서는 실제로 순수하게 테스트할 수 있는 것만 다룬다:
  - format_stop_reason: 엔진의 stop_reason 값을 한글로 바꾸는 순수 함수 (R17).
  - MainWindow.closeEvent: 실행 중 창을 닫아도 브라우저를 남기지 않아야 한다
    는 규칙(R16)은 QMessageBox 대화상자 반환값과 EngineBridge.is_running만
    스텁으로 바꾸면 실제 위젯으로 검증할 수 있다. Qt 내부를 흉내 내지는
    않는다.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from desktop.app import STOP_REASON_KO, format_stop_reason


# ---------------- R17: stop_reason → 한글 ----------------


@pytest.mark.parametrize("reason", sorted(STOP_REASON_KO))
def test_named_stop_reasons_map_to_non_empty_korean_text(reason):
    assert format_stop_reason(reason) == STOP_REASON_KO[reason]
    assert format_stop_reason(reason) != reason


def test_all_six_named_reasons_from_runsummary_are_covered():
    # engine/events.py RunSummary.stop_reason 주석에 나열된 값들.
    named = {"budget", "exhausted", "user", "blocked", "not_logged_in", "error"}
    assert named <= STOP_REASON_KO.keys()


def test_unmapped_reason_falls_back_to_raw_string_without_crashing():
    # RunSummary.stop_reason의 dataclass 기본값 "unknown"을 포함해, 목록에
    # 없는 어떤 값이 와도 원문 그대로 보여준다 — 예외를 내지 않는다.
    assert format_stop_reason("unknown") == "unknown"
    assert format_stop_reason("이상한_사유") == "이상한_사유"


# ---------------- R16: 실행 중 창 닫기 ----------------


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # AppPaths.for_app()의 기본 경로(LOCALAPPDATA)가 아니라 임시 디렉터리를
    # 쓰게 해서 실제 사용자 데이터 폴더를 건드리지 않는다.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from desktop.app import MainWindow

    win = MainWindow()
    yield win
    win.close()


def _close_event():
    from PyQt6.QtGui import QCloseEvent

    return QCloseEvent()


def test_close_event_accepts_immediately_when_nothing_is_running(window):
    event = _close_event()
    window.closeEvent(event)
    assert event.isAccepted() is True
    assert window._close_pending is False


def test_close_event_stays_open_when_operator_declines(window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(window.bridge, "is_running", lambda: True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )

    event = _close_event()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window._close_pending is False


def test_close_event_requests_stop_through_the_bridge_and_stays_open(window, monkeypatch):
    """확인하면 ■ 버튼과 같은 경로(EngineBridge.request_stop)로 정지를 요청하고,
    창은 즉시 닫히지 않는다 — _run_engine의 finally가 브라우저를 정리할 때까지
    데몬 스레드가 프로세스와 함께 죽지 않게 하기 위해서다."""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(window.bridge, "is_running", lambda: True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    routed = []
    monkeypatch.setattr(window.bridge, "request_stop", lambda cb: routed.append(cb))

    class FakeRunner:
        def request_stop(self) -> None:
            pass

    window.runner = FakeRunner()

    event = _close_event()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window._close_pending is True
    assert routed, "정지 요청이 EngineBridge.request_stop을 거치지 않았다"


def test_close_event_does_not_reprompt_while_a_stop_is_already_pending(window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(window.bridge, "is_running", lambda: True)
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes,
    )
    window._close_pending = True

    event = _close_event()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert asked == []


def test_finished_closes_the_window_once_a_close_is_pending(window):
    window._close_pending = True
    closed = []
    window.close = lambda: closed.append(True)

    window.on_finished(None)

    assert closed == [True]


def test_finished_does_not_close_the_window_without_a_pending_close(window):
    closed = []
    window.close = lambda: closed.append(True)

    window.on_finished(None)

    assert closed == []


def test_failed_closes_the_window_once_a_close_is_pending_and_skips_the_dialog(
    window, monkeypatch
):
    from PyQt6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(1))
    window._close_pending = True
    closed = []
    window.close = lambda: closed.append(True)

    window.on_failed("boom")

    assert closed == [True]
    assert shown == []
