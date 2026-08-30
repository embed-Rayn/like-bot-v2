"""tools/dryrun.py의 순수하게 테스트 가능한 부분.

main_async는 실제 네트워크 · 브라우저를 쓰므로(§8 층 3과 같은 이유) 여기서
다루지 않는다. --blogs 상한 검증(I4)만 확인한다.
"""
from __future__ import annotations

import sys

import pytest

from tools import dryrun


def test_blogs_over_the_cap_is_rejected(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "acct", "kw", "--blogs", "999"])
    with pytest.raises(SystemExit):
        dryrun.main()
    assert "--blogs" in capsys.readouterr().err


def test_blogs_zero_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "acct", "kw", "--blogs", "0"])
    with pytest.raises(SystemExit):
        dryrun.main()


def test_blogs_within_range_reaches_main_async(monkeypatch):
    captured = {}

    def fake_run(coro):
        captured["called"] = True
        coro.close()    # 실행하지 않고 미사용 코루틴 경고만 막는다

    monkeypatch.setattr(dryrun.asyncio, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["dryrun.py", "acct", "kw", "--blogs", "3"])

    dryrun.main()

    assert captured.get("called") is True


def test_default_blogs_count_is_within_the_cap():
    assert dryrun.MAX_BLOGS >= 5    # 기본값 --blogs=5가 스스로 거부되지 않는다
