"""tools/login.py의 순수하게 테스트 가능한 부분 (test_dryrun_tool.py와 같은 이유)."""
from __future__ import annotations

import sys

import pytest

from tools import login


def test_timeout_over_the_cap_is_rejected(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["login.py", "acct", "--timeout", "99999"])
    with pytest.raises(SystemExit):
        login.main()
    assert "--timeout" in capsys.readouterr().err


def test_timeout_zero_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["login.py", "acct", "--timeout", "0"])
    with pytest.raises(SystemExit):
        login.main()


def test_account_is_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["login.py"])
    with pytest.raises(SystemExit):
        login.main()


def test_default_timeout_is_within_the_cap():
    assert 1 <= login.MANUAL_LOGIN_TIMEOUT_S <= login.MAX_TIMEOUT_S


def test_failed_login_exits_nonzero(monkeypatch):
    monkeypatch.setattr(login.asyncio, "run", lambda coro: (coro.close(), False)[1])
    monkeypatch.setattr(sys, "argv", ["login.py", "acct"])

    with pytest.raises(SystemExit) as exc:
        login.main()

    assert exc.value.code == 1


def test_successful_login_exits_zero(monkeypatch):
    monkeypatch.setattr(login.asyncio, "run", lambda coro: (coro.close(), True)[1])
    monkeypatch.setattr(sys, "argv", ["login.py", "acct"])

    with pytest.raises(SystemExit) as exc:
        login.main()

    assert exc.value.code == 0
