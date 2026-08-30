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


# ---------------- CRITICAL 2: stop/close 요청이 Runner 생성 전에 사라지지 않는다 ----------------


def test_on_stop_sets_the_flag_and_updates_the_label_even_without_a_runner(window):
    """session.open()이 아직 끝나지 않아 self.runner가 없는 동안 ■ 를 눌러도
    요청이 조용히 버려지지 않는다 — 화면에도 눈에 띄게 반영된다."""
    assert window.runner is None
    window.on_stop()
    assert window._stop_requested is True
    assert "정지" in window.summary_label.text()


def test_on_stop_still_routes_through_the_bridge_when_a_runner_exists(window, monkeypatch):
    routed = []
    monkeypatch.setattr(window.bridge, "request_stop", lambda cb: routed.append(cb))

    class FakeRunner:
        def request_stop(self) -> None:
            pass

    window.runner = FakeRunner()
    window.on_stop()

    assert window._stop_requested is True
    assert routed, "정지 요청이 EngineBridge.request_stop을 거치지 않았다"


def test_close_event_retries_the_stop_when_already_pending(window, monkeypatch):
    """이미 정지를 기다리는 중에 다시 닫으려는 시도가, 재확인 없이 정지
    요청 자체는 다시 보낸다 (첫 요청이 유실됐을 수 있으므로)."""
    monkeypatch.setattr(window.bridge, "is_running", lambda: True)
    routed = []
    monkeypatch.setattr(window.bridge, "request_stop", lambda cb: routed.append(cb))

    class FakeRunner:
        def request_stop(self) -> None:
            pass

    window.runner = FakeRunner()
    window._close_pending = True

    event = _close_event()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window._stop_requested is True
    assert routed, "재시도된 정지 요청이 브리지를 거치지 않았다"


# ---------------- MINOR: LikeResultEvent는 자기 키워드 패널로만 간다 ----------------


def test_like_result_event_routes_to_its_own_panel_not_always_the_first(window):
    from engine.events import LikeResultEvent

    window.panels[0].keyword_input.setText("kw1")
    window.panels[1].keyword_input.setText("kw2")

    window.on_event(LikeResultEvent(keyword="kw2", blog_id="b2", log_no="1",
                                    outcome="error"))

    assert "b2/1" in window.panels[1].log_view.toPlainText()
    assert "b2/1" not in window.panels[0].log_view.toPlainText()


# ---------------- I5: 기간 · 상한 · 속도 제한도 config.json에 저장된다 ----------------


def test_save_and_reload_persists_settings_beyond_account_and_keywords(
    app, tmp_path, monkeypatch
):
    """스펙 §6.5: 키워드 · 기간 · 방문 상한 · 블로그당 공감 수 · 속도 제한 ·
    제외 단어 전부가 config.json에 남아야 한다. 특히 속도 제한(§7.3)이
    저장되지 않으면 조심스럽게 낮춰 둔 값이 다음 실행마다 조용히
    기본값(6.0)으로 되돌아간다."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from desktop.app import MainWindow
    from engine.config import RunConfig

    win1 = MainWindow()
    config, errors = RunConfig.validate({
        "account": "acct",
        "keywords": ["kw1"],
        "excludes": ["ex1"],
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "blog_limit": 42,
        "likes_per_blog": 2,
        "likes_per_minute": 3.5,
        "dry_run": False,
    })
    assert errors == []
    win1._save_config(config)
    win1.close()

    win2 = MainWindow()
    assert win2.start_date_input.text() == "2026-01-01"
    assert win2.end_date_input.text() == "2026-01-02"
    assert win2.blog_limit_input.value() == 42
    assert win2.likes_input.value() == 2
    assert win2.rate_input.value() == 3.5
    win2.close()


def test_dry_run_is_never_written_to_the_config_file(app, tmp_path, monkeypatch):
    """문자열로 왕복하면 "False"가 truthy가 되는 함정이 있으므로 dry_run은
    아예 파일에 담지 않는다."""
    import json

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from desktop.app import MainWindow
    from engine.config import RunConfig

    win = MainWindow()
    config, errors = RunConfig.validate({
        "account": "acct",
        "keywords": ["kw1"],
        "excludes": [],
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "blog_limit": 1,
        "likes_per_blog": 1,
        "likes_per_minute": 1.0,
        "dry_run": True,
    })
    assert errors == []
    win._save_config(config)
    win.close()

    saved = json.loads(win.paths.config_file.read_text(encoding="utf-8"))
    assert "dry_run" not in saved


# ---------------- I3: 드라이런 요약은 눈에 띄게 표시되어야 한다 ----------------


def test_summary_label_is_prefixed_for_a_dry_run(window):
    from engine.events import RunFinished, RunSummary

    window.on_event(RunFinished(RunSummary(
        run_id="r1", blogs_done=3, likes_ok=9, likes_tried=9,
        stop_reason="exhausted", dry_run=True,
    )))

    assert window.summary_label.text().startswith("[드라이런]")


def test_summary_label_has_no_dry_run_marker_for_a_real_run(window):
    from engine.events import RunFinished, RunSummary

    window.on_event(RunFinished(RunSummary(
        run_id="r1", blogs_done=3, likes_ok=9, likes_tried=9,
        stop_reason="exhausted", dry_run=False,
    )))

    assert "드라이런" not in window.summary_label.text()


async def test_run_engine_closes_session_and_skips_runner_when_stop_requested_first(
    window, monkeypatch
):
    """session.open()이 끝난 시점에 이미 정지가 요청돼 있었다면, Runner를
    만들지 않고 세션을 정리한 뒤 그대로 반환해야 한다. History는 open()
    이후에만 만들어지므로(MINOR: 커넥션 누수 방지) 이 경로에서는 아예
    생성되지 않는다 — 만들어졌다가 닫히지 않고 새는 것보다 안전하다."""
    from engine.config import RunConfig

    closed = {"session": False}
    history_created = {"called": False}

    class FakeSession:
        page = None

        async def open(self, account, password_supplier):
            return self

        async def close(self):
            closed["session"] = True

    class FakeHistory:
        def close(self):
            pass

    def fake_history_ctor(path):
        history_created["called"] = True
        return FakeHistory()

    monkeypatch.setattr("desktop.app.BrowserSession", lambda paths: FakeSession())
    monkeypatch.setattr("desktop.app.History", fake_history_ctor)

    config, errors = RunConfig.validate({
        "account": "acct",
        "keywords": ["kw"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": 10,
        "likes_per_blog": 3,
        "likes_per_minute": 6.0,
        "dry_run": False,
    })
    assert errors == []

    window._stop_requested = True
    result = await window._run_engine(config, "pw", lambda e: None)

    assert result is None
    assert closed == {"session": True}
    assert history_created["called"] is False


async def test_run_engine_does_not_leak_a_history_connection_on_non_login_failure(
    window, monkeypatch
):
    """MINOR: session.open()이 LoginError가 아닌 예외(브라우저 바이너리
    누락 등)로 실패해도, History는 open() 성공 이후에만 만들어지므로 닫을
    커넥션 자체가 없다 — 새는 커넥션이 생기지 않는다."""
    class FakeSession:
        async def open(self, account, password_supplier):
            raise RuntimeError("chromium binary missing")

    history_created = {"called": False}

    def fake_history_ctor(path):
        history_created["called"] = True
        return object()

    monkeypatch.setattr("desktop.app.BrowserSession", lambda paths: FakeSession())
    monkeypatch.setattr("desktop.app.History", fake_history_ctor)

    from engine.config import RunConfig

    config, errors = RunConfig.validate({
        "account": "acct",
        "keywords": ["kw"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": 10,
        "likes_per_blog": 3,
        "likes_per_minute": 6.0,
        "dry_run": False,
    })
    assert errors == []

    with pytest.raises(RuntimeError):
        await window._run_engine(config, "pw", lambda e: None)

    assert history_created["called"] is False
    assert window.runner is None
