"""desktop/app.py는 대부분 배선(wiring)이라 단위 테스트로 다루기 어렵다.

여기서는 실제로 순수하게 테스트할 수 있는 것만 다룬다:
  - format_stop_reason: 엔진의 stop_reason 값을 한글로 바꾸는 순수 함수 (R17).
  - MainWindow.closeEvent: 실행 중 창을 닫아도 브라우저를 남기지 않아야 한다
    는 규칙(R16)은 QMessageBox 대화상자 반환값과 EngineBridge.is_running만
    스텁으로 바꾸면 실제 위젯으로 검증할 수 있다. Qt 내부를 흉내 내지는
    않는다.
"""
import json

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


# ---------------- MINOR: 하드코딩 대신 engine의 공유 상수/값을 쓴다 ----------------


def test_page_collected_shows_plus_suffix_at_the_shared_cap(window):
    from engine.events import PageCollected
    from engine.models import TOTAL_COUNT_CAP

    window.panels[0].keyword_input.setText("kw1")
    window.on_event(PageCollected(
        keyword="kw1", page=1, found=7, queued=7, total_count=TOTAL_COUNT_CAP,
    ))

    assert f"{TOTAL_COUNT_CAP}+" in window.panels[0].status_label.text()


def test_page_collected_has_no_plus_suffix_below_the_cap(window):
    from engine.events import PageCollected
    from engine.models import TOTAL_COUNT_CAP

    window.panels[0].keyword_input.setText("kw1")
    window.on_event(PageCollected(
        keyword="kw1", page=1, found=7, queued=7, total_count=TOTAL_COUNT_CAP - 1,
    ))

    assert "+" not in window.panels[0].status_label.text()


def test_like_result_event_uses_the_shared_outcome_values(window):
    """LikeOutcome.SUCCESS/ALREADY_LIKED와 어긋난 문자열 리터럴이 아니라 실제
    enum 값을 기준으로 성공/이미공감을 걸러내는지 확인한다."""
    from engine.events import LikeResultEvent
    from engine.models import LikeOutcome

    window.panels[0].keyword_input.setText("kw1")

    window.on_event(LikeResultEvent(keyword="kw1", blog_id="b1", log_no="1",
                                    outcome=LikeOutcome.SUCCESS.value))
    window.on_event(LikeResultEvent(keyword="kw1", blog_id="b2", log_no="2",
                                    outcome=LikeOutcome.ALREADY_LIKED.value))
    window.on_event(LikeResultEvent(keyword="kw1", blog_id="b3", log_no="3",
                                    outcome=LikeOutcome.ERROR.value))

    text = window.panels[0].log_view.toPlainText()
    assert "b1/1" not in text
    assert "b2/2" not in text
    assert "b3/3" in text


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

        async def open(self, account, password_supplier, **_kwargs):
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
        async def open(self, account, password_supplier, **_kwargs):
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


# ---------------- 키워드별 실행 버튼 ----------------
#
# 버튼은 패널마다 있지만 실행은 여전히 계정 단위로 하나다(결정 4). 패널의
# ▶는 "이 키워드 하나로만 실행"을 뜻한다 — 공감 속도 제한이 계정 단위
# 하나뿐이라, 여러 실행을 동시에 돌리면 예산만 흩어지고 계정만 위험해진다.


@pytest.fixture
def started(window, monkeypatch):
    """on_run을 엔진 없이 돌린다. 시작된 실행의 RunConfig를 붙잡아 돌려준다."""
    captured = {}

    monkeypatch.setattr("desktop.app.keyring.set_password", lambda *a, **k: None)
    monkeypatch.setattr("desktop.app.keyring.get_password", lambda *a, **k: "pw")

    def fake_run_engine(config, password, emit):
        captured["config"] = config

    monkeypatch.setattr(window, "_run_engine", fake_run_engine)
    monkeypatch.setattr(window.bridge, "start", lambda factory: factory(lambda _e: None))

    window.account_input.setText("someaccount")
    window.panels[0].keyword_input.setText("헬스장")
    window.panels[1].keyword_input.setText("필라테스")
    return window, captured


def test_panel_start_button_runs_only_that_panels_keyword(started):
    window, captured = started

    window.panels[1].start_button.click()

    assert captured["config"].keywords == ["필라테스"]


def test_top_run_button_still_runs_every_keyword(started):
    window, captured = started

    window.run_button.click()

    assert captured["config"].keywords == ["헬스장", "필라테스"]


def test_panel_buttons_are_visible(window):
    assert window.panels[0].start_button.isHidden() is False
    assert window.panels[0].stop_button.isHidden() is False


def test_top_stop_button_turns_red_while_running(started):
    window, _ = started
    assert "#b00020" not in window.stop_button.styleSheet()

    window.run_button.click()

    assert "#b00020" in window.stop_button.styleSheet()


# ---------------- 로그인: 저장된 비밀번호가 없어도 실행된다 ----------------
#
# 설계 결정 3은 세션 재사용이 핵심이다. 그런데 예전 게이트는 저장된 비밀번호가
# 없으면 세션이 멀쩡해도 실행을 거부했다 — 세션만으로 돌 수 있다는 설계가
# 화면에서 막혀 있었다. 비밀번호는 세션이 없을 때만 필요하다.


@pytest.fixture
def no_stored_password(window, monkeypatch):
    """저장된 비밀번호가 없는 상태. 모달 경고는 테스트를 멈추므로 가로챈다."""
    from PyQt6.QtWidgets import QMessageBox

    captured = {"warnings": []}

    monkeypatch.setattr("desktop.app.keyring.set_password", lambda *a, **k: None)
    monkeypatch.setattr("desktop.app.keyring.get_password", lambda *a, **k: None)
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: captured["warnings"].append(a[2:])
    )

    def fake_run_engine(config, password, emit):
        captured["config"] = config
        captured["password"] = password

    monkeypatch.setattr(window, "_run_engine", fake_run_engine)
    monkeypatch.setattr(window.bridge, "start", lambda factory: factory(lambda _e: None))

    window.account_input.setText("someaccount")
    window.panels[0].keyword_input.setText("헬스장")
    return window, captured


def test_run_starts_when_no_password_is_stored(no_stored_password):
    """세션이 살아 있을 수 있으므로 비밀번호가 없다고 막지 않는다."""
    window, captured = no_stored_password

    window.run_button.click()

    assert "config" in captured, (
        f"저장된 비밀번호가 없다고 실행을 거부했습니다: {captured['warnings']}"
    )
    assert captured["password"] == "", (
        f"비밀번호가 없으면 빈 문자열이어야 합니다: {captured['password']!r}"
    )


def test_missing_password_does_not_warn(no_stored_password):
    window, captured = no_stored_password

    window.run_button.click()

    assert not captured["warnings"], "비밀번호가 없다고 경고를 띄웠습니다."


# ---------------- 로그인이 막히면 운영자에게 알린다 ----------------


def test_global_log_line_also_shows_in_the_summary_label(window):
    """키워드가 빈 로그는 전역 메시지다 — 패널 로그에 묻히면 안 된다.

    캡차 안내가 여기로 온다. 브라우저 창에서 무엇을 해야 하는지 모른 채
    운영자가 기다리는 일이 없어야 한다.
    """
    from engine.events import LogLine

    window.on_event(LogLine("", "브라우저 창에서 추가 확인(이미지) 문제를 풀어 주세요."))

    assert "추가 확인" in window.summary_label.text()


class _StopHere(Exception):
    """가짜 세션이 open()에서 실행을 끊기 위한 신호."""


async def test_run_engine_gives_the_session_a_challenge_callback(window, monkeypatch):
    """session.open()에 안내 콜백이 전달되고, 그 콜백은 전역 로그를 낸다."""
    seen = {}

    class _FakeSession:
        def __init__(self, _paths):
            pass

        async def open(self, _account, _supplier, **kwargs):
            seen["on_challenge"] = kwargs.get("on_challenge")
            raise _StopHere()

        async def close(self):
            pass

    monkeypatch.setattr("desktop.app.BrowserSession", _FakeSession)

    window.account_input.setText("someaccount")
    window.panels[0].keyword_input.setText("헬스장")
    from desktop.app import RunConfig

    config, errors = RunConfig.validate(window._collect_raw(["헬스장"]))
    assert not errors, errors

    events = []
    with pytest.raises(_StopHere):
        await window._run_engine(config, "pw", events.append)

    callback = seen.get("on_challenge")
    assert callback is not None, "session.open()에 안내 콜백을 넘기지 않았습니다."

    callback("추가 확인이 필요합니다")
    assert events, "콜백을 불러도 아무 이벤트가 나오지 않았습니다."
    assert "추가 확인" in events[0].text
    assert events[0].keyword == "", "안내는 전역 메시지여야 합니다."


# ---------------- 배포본 부트스트랩 · 브라우저 확보 ----------------


def test_bootstrap_points_a_frozen_build_at_the_app_browser_folder(tmp_path, monkeypatch):
    import os
    import sys

    from desktop.app import bootstrap
    from engine.paths import AppPaths

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    paths = AppPaths.for_app(tmp_path)

    bootstrap(paths)

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(paths.browsers_dir)


def test_bootstrap_installs_a_crash_handler(tmp_path, monkeypatch):
    """console=False 빌드에서는 트레이스백이 어디에도 남지 않는다."""
    import sys

    from desktop.app import bootstrap
    from engine.paths import AppPaths

    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)

    bootstrap(AppPaths.for_app(tmp_path))

    assert sys.excepthook is not sys.__excepthook__


def test_crash_log_is_written_under_the_app_log_folder(tmp_path):
    from desktop.app import write_crash_log
    from engine.paths import AppPaths

    paths = AppPaths.for_app(tmp_path)

    written = write_crash_log(paths, "Traceback (most recent call last):\nBoom")

    assert written.parent == paths.log_dir
    assert "Boom" in written.read_text(encoding="utf-8")


async def test_run_engine_secures_the_browser_before_opening_the_session(
    window, monkeypatch
):
    """브라우저가 없으면 session.open()은 playwright 예외로 죽는다 — 그 전에 받는다."""
    from engine.config import RunConfig
    from engine.events import LogLine

    order: list[str] = []

    async def fake_ensure(on_line):
        order.append("ensure")
        on_line("브라우저 내려받는 중")

    class FakeSession:
        page = None

        async def open(self, account, password_supplier, **_kwargs):
            order.append("open")
            return self

        async def close(self):
            pass

    monkeypatch.setattr("desktop.app.ensure_chromium", fake_ensure)
    monkeypatch.setattr("desktop.app.BrowserSession", lambda paths: FakeSession())

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

    window._stop_requested = True  # open() 직후 반환시켜 Runner까지 가지 않게 한다
    events: list[object] = []
    await window._run_engine(config, "pw", events.append)

    assert order == ["ensure", "open"]
    assert any(
        isinstance(e, LogLine) and e.text == "브라우저 내려받는 중" for e in events
    )


# ---------------- 실행 로그 파일 ----------------


def _valid_config(**over):
    from engine.config import RunConfig

    raw = {
        "account": "acct",
        "keywords": ["kw"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": 10,
        "likes_per_blog": 3,
        "likes_per_minute": 6.0,
        "dry_run": False,
    }
    raw.update(over)
    config, errors = RunConfig.validate(raw)
    assert errors == [], errors
    return config


def _run_logs(window):
    return sorted(window.paths.log_dir.glob("run-*.jsonl"))


def _stub_session_that_stops(monkeypatch, on_open=None):
    """세션을 가짜로 바꿔 Runner 직전에 실행을 멈춘다."""

    class FakeSession:
        page = None

        def __init__(self, _paths):
            pass

        async def open(self, _account, _supplier, **kwargs):
            if on_open is not None:
                on_open(kwargs)

        async def close(self):
            pass

    async def fake_ensure(on_line):
        on_line("브라우저 확인 중")

    monkeypatch.setattr("desktop.app.ensure_chromium", fake_ensure)
    monkeypatch.setattr("desktop.app.BrowserSession", FakeSession)


async def test_run_engine_writes_a_log_file_for_the_run(window, monkeypatch):
    _stub_session_that_stops(monkeypatch)
    window._stop_requested = True  # Runner까지 가지 않게 한다

    await window._run_engine(_valid_config(), "pw", lambda _e: None)

    files = _run_logs(window)
    assert len(files) == 1, "실행 로그 파일이 남지 않았습니다."
    header = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert header["type"] == "RunStarted"
    assert header["account"] == "acct"
    assert header["keywords"] == ["kw"]


async def test_run_engine_logs_events_that_also_reach_the_ui(window, monkeypatch):
    """파일 기록은 UI로 가는 이벤트를 가로채지 않는다 — 갈래를 하나 더 낼 뿐이다."""
    _stub_session_that_stops(monkeypatch)
    window._stop_requested = True

    events: list[object] = []
    await window._run_engine(_valid_config(), "pw", events.append)

    from engine.events import LogLine

    assert any(isinstance(e, LogLine) and e.text == "브라우저 확인 중" for e in events)
    written = _run_logs(window)[0].read_text(encoding="utf-8")
    assert "브라우저 확인 중" in written


async def test_run_engine_logs_the_login_challenge_notice(window, monkeypatch):
    """로그인이 막혀 중단된 실행이야말로 나중에 들여다볼 이유가 크다."""

    def raise_challenge(kwargs):
        kwargs["on_challenge"]("추가 확인이 필요합니다")

    _stub_session_that_stops(monkeypatch, on_open=raise_challenge)
    window._stop_requested = True

    await window._run_engine(_valid_config(), "pw", lambda _e: None)

    written = _run_logs(window)[0].read_text(encoding="utf-8")
    assert "추가 확인이 필요합니다" in written


async def test_run_engine_closes_the_log_when_the_run_blows_up(window, monkeypatch):
    """예외로 죽어도 그때까지의 기록은 파일에 남아 있어야 한다."""

    def boom(_kwargs):
        raise _StopHere()

    _stub_session_that_stops(monkeypatch, on_open=boom)

    with pytest.raises(_StopHere):
        await window._run_engine(_valid_config(), "pw", lambda _e: None)

    written = _run_logs(window)[0].read_text(encoding="utf-8")
    assert "브라우저 확인 중" in written


async def test_run_engine_reuses_one_run_id_for_log_and_runner(window, monkeypatch):
    _stub_session_that_stops(monkeypatch)
    window._stop_requested = True

    await window._run_engine(_valid_config(), "pw", lambda _e: None)

    path = _run_logs(window)[0]
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert path.name.endswith(f"-{header['run_id']}.jsonl")


def test_log_folder_button_opens_the_log_directory(window, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "desktop.app.QDesktopServices.openUrl", lambda url: opened.append(url)
    )

    window.log_button.click()

    assert window.paths.log_dir.is_dir(), "폴더가 없으면 탐색기가 빈손으로 열린다."
    assert opened, "로그 폴더 버튼이 아무것도 열지 않았습니다."
    assert opened[0].toLocalFile().replace("/", "\\").rstrip("\\") == str(
        window.paths.log_dir
    )


# ---- 중단 배너는 터진 키워드에만 뜬다 ----
# 예전에는 Aborted를 무조건 4개 패널 전부로 방송해서, 이번 실행에 참여하지도
# 않은 빈 패널까지 빨간 "중단"이 뜨고 원인 키워드는 어디에도 남지 않았다.


def _arm(window, keywords):
    for panel, kw in zip(window.panels, keywords):
        panel.keyword_input.setText(kw)
    window._participating = {k for k in keywords if k}


def test_abort_banner_only_on_the_keyword_that_tripped(window):
    from engine.events import Aborted

    _arm(window, ["kw1", "kw2", "kw3", "kw4"])
    window.on_event(Aborted("5건 연속 실패했습니다.", keyword="kw2"))

    assert not window.panels[1].alert_label.isHidden()
    assert "5건 연속" in window.panels[1].alert_label.text()
    assert [p.alert_label.text() for p in window.panels if p.keyword() != "kw2"] == ["", "", ""]


def test_other_participating_panels_say_they_stopped(window):
    from engine.events import Aborted

    _arm(window, ["kw1", "kw2", "", ""])
    window.on_event(Aborted("5건 연속 실패했습니다.", keyword="kw2"))

    assert "중단" in window.panels[0].status_label.text()


def test_panels_not_in_the_run_are_left_alone(window):
    from engine.events import Aborted

    _arm(window, ["kw1", "kw2", "kw3", "kw4"])
    window._participating = {"kw1", "kw2"}
    before = window.panels[3].status_label.text()
    window.on_event(Aborted("5건 연속 실패했습니다.", keyword="kw2"))

    assert window.panels[3].alert_label.text() == ""
    assert window.panels[3].status_label.text() == before


def test_keywordless_abort_still_reaches_every_participating_panel(window):
    """검색 이전 단계(세션 등)에서 난 중단은 원인 키워드가 없다."""
    from engine.events import Aborted

    _arm(window, ["kw1", "kw2", "", ""])
    window.on_event(Aborted("세션이 더 이상 로그인 상태가 아닙니다."))

    assert "중단" in window.panels[0].alert_label.text()
    assert "중단" in window.panels[1].alert_label.text()


def test_like_result_line_shows_the_timeout_stage(window):
    from engine.events import LikeResultEvent

    _arm(window, ["kw1", "", "", ""])
    window.on_event(LikeResultEvent(keyword="kw1", blog_id="b1", log_no="9",
                                    outcome="timeout", detail="클릭 후 on 확인"))

    assert "클릭 후 on 확인" in window.panels[0].log_view.toPlainText()


# ---- 전역 로그(로그인 안내 등)는 빈 패널로 새지 않는다 ----
# 실측 2026-09-10 (run-20260910-195029): 키워드 1로만 실행했는데 "브라우저
# 창에서 로그인을 완료해 주세요"가 3번 패널에 떴다. LogLine.keyword=""와
# 빈 키워드 칸의 keyword()가 똑같이 ""라서, _panel_for("")가 "키워드가 비어
# 있는 첫 패널"을 원인 패널로 골라 버린 것이다.


def test_panel_lookup_never_matches_an_empty_keyword_box(window):
    _arm(window, ["kw1", "kw2", "", ""])

    assert window._panel_for("") is None


def test_global_log_line_does_not_land_in_an_empty_panel(window):
    from engine.events import LogLine

    _arm(window, ["kw1", "", "", ""])
    window.on_event(LogLine("", "브라우저 창에서 로그인을 완료해 주세요."))

    assert "로그인을 완료" in window.panels[0].log_view.toPlainText()
    assert window.panels[2].log_view.toPlainText() == ""


def test_global_log_line_reaches_every_participating_panel(window):
    from engine.events import LogLine

    _arm(window, ["kw1", "kw2", "kw3", "kw4"])
    window._participating = {"kw1", "kw2"}
    window.on_event(LogLine("", "브라우저 창에서 로그인을 완료해 주세요."))

    assert "로그인을 완료" in window.panels[0].log_view.toPlainText()
    assert "로그인을 완료" in window.panels[1].log_view.toPlainText()
    assert window.panels[2].log_view.toPlainText() == ""
    assert window.panels[3].log_view.toPlainText() == ""


def test_global_log_line_falls_back_to_the_first_panel_before_a_run(window):
    """실행 정보가 아직 없으면(창을 열자마자) 알릴 곳은 1번 패널뿐이다."""
    from engine.events import LogLine

    window.on_event(LogLine("", "브라우저를 내려받는 중입니다."))

    assert "내려받는 중" in window.panels[0].log_view.toPlainText()


# ---- 이번 실행에 없는 패널은 그렇다고 말한다 ----
# 패널 ▶는 "이 키워드 하나로만 실행"이다(결정 4). 그런데 참여하지 않은
# 패널은 상태 줄이 "대기 중" 그대로여서 고장난 것과 구분되지 않았다.


def test_panel_left_out_of_the_run_says_so(started):
    window, _ = started

    window.panels[0].start_button.click()

    assert "이번 실행" in window.panels[1].status_label.text()
    assert window.panels[1].status_label.text() != "대기 중"


def test_panel_in_the_run_is_not_marked_as_left_out(started):
    window, _ = started

    window.panels[0].start_button.click()

    assert "이번 실행" not in window.panels[0].status_label.text()
