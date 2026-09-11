"""수동 로그인 — 창을 띄우고, 사람이 직접 로그인한 세션을 저장한다.

네이버는 자동 입력 로그인에 "보안을 위해 추가 확인" 화면(이미지 문제)을 띄운다
(2026-08-31 관측). 사람이 풀어야 하는 화면이므로 최초 1회는 여기서 직접
로그인한다. 저장된 세션 덕분에 이후 드라이런·실행은 로그인 페이지를 거치지
않는다 (설계 결정 3의 storage_state 재사용).

비밀번호를 묻지 않는다 — 이 도구는 창을 열어 줄 뿐이다. 앱도 마찬가지다
(자동 입력 폐지, 2026-09-11).

기다리는 동안 창을 닫으면 즉시 중단된다. 타이머는 잊고 자리를 뜬 경우의
뒷받침일 뿐이므로 넉넉하다 — 보호조치 해제는 5분 안에 끝나지 않는다.

    python tools/login.py <네이버ID> [--timeout 300]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from engine.paths import AppPaths
from engine.session import MANUAL_LOGIN_TIMEOUT_S, BrowserSession

MAX_TIMEOUT_S = 3600


async def main_async(account: str, timeout_s: float) -> bool:
    paths = AppPaths.for_app()
    paths.ensure()
    session = BrowserSession(paths)
    return await session.bootstrap_manual(account, timeout_s=timeout_s, notify=print)


def main() -> None:
    parser = argparse.ArgumentParser(description="like-bot-v2 수동 로그인")
    parser.add_argument("account", help="네이버 ID (세션 파일 이름이 된다)")
    parser.add_argument(
        "--timeout", type=float, default=MANUAL_LOGIN_TIMEOUT_S,
        help=f"로그인 완료를 기다리는 초 (1 ~ {MAX_TIMEOUT_S})",
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= MAX_TIMEOUT_S:
        parser.error(f"--timeout은 1 이상 {MAX_TIMEOUT_S} 이하여야 합니다.")
    ok = asyncio.run(main_async(args.account, args.timeout))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
