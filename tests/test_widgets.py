import pytest

pytest.importorskip("PyQt6.QtWidgets")

from desktop.widgets import KeywordPanel


@pytest.fixture
def app():
    from PyQt6.QtWidgets import QApplication

    existing = QApplication.instance()
    yield existing or QApplication([])


def test_panel_starts_idle_with_stop_disabled(app):
    panel = KeywordPanel(1)
    assert panel.start_button.isEnabled() is True
    assert panel.stop_button.isEnabled() is False


def test_set_running_swaps_button_states(app):
    panel = KeywordPanel(1)
    panel.set_running(True)
    assert panel.start_button.isEnabled() is False
    assert panel.stop_button.isEnabled() is True

    panel.set_running(False)
    assert panel.start_button.isEnabled() is True
    assert panel.stop_button.isEnabled() is False


def test_keyword_is_read_from_the_input(app):
    panel = KeywordPanel(1)
    panel.keyword_input.setText("  헬스장  ")
    assert panel.keyword() == "헬스장"


def test_append_log_accumulates(app):
    panel = KeywordPanel(1)
    panel.append_log("첫 줄")
    panel.append_log("둘째 줄")
    text = panel.log_view.toPlainText()
    assert "첫 줄" in text and "둘째 줄" in text


def test_alert_is_visible_only_when_set(app):
    """폴백·차단 경고는 눈에 띄어야 한다 — 조용히 잘리지 않게.

    isVisible()은 panel 자체가 화면에 show()되지 않으면(이 테스트에서는
    그렇다) 항상 False라서, set_alert에서 setVisible 호출을 지워도 첫
    assert가 여전히 통과했다. isVisibleTo(panel)은 '부모(panel)가 보이게
    되면 이 위젯도 보일 것인가'를 판정하므로 명시적 hide/show 상태를
    실제로 검증한다.
    """
    panel = KeywordPanel(1)
    assert panel.alert_label.isVisibleTo(panel) is False
    panel.set_alert("네이버 응답 형식이 바뀐 것 같습니다")
    assert panel.alert_label.isVisibleTo(panel) is True
    assert panel.alert_label.text() != ""


def test_panel_uses_a_layout_not_fixed_geometry(app):
    """레거시의 절대좌표 배치를 반복하지 않는다."""
    panel = KeywordPanel(1)
    assert panel.layout() is not None
