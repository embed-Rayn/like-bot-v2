"""chromium 설치 확인과 최초 1회 내려받기.

exe로 배포하면 파이썬도 pip도 없어서 `python -m playwright install`을 부를 수
없다. playwright가 자기 CLI를 돌릴 때 쓰는 node 드라이버를 직접 실행한다 —
`python -m playwright install`이 하는 일과 같은 명령이다.

chromium은 exe에 넣지 않는다. 압축 전 428MB(내려받기 ~150MB)라 exe가 그만큼
커지고, playwright는 어차피 버전마다 리비전을 핀해 두므로 기본 위치
(LOCALAPPDATA 아래 ms-playwright)에 두면 이미 받아 둔 것을 그대로 쓴다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# 다운로드 진행 줄은 계속 흘러야 한다 — 150MB를 받는 동안 화면이 멈춰 보이면
# 운영자는 앱이 죽은 줄 안다.
OnLine = Callable[[str], None]

# 실패 메시지에 붙일 마지막 출력 줄 수. 전체를 붙이면 진행바 수백 줄이 딸려와
# 정작 원인이 파묻힌다.
ERROR_TAIL_LINES = 5


# 브라우저를 못 찾는다는 신고는 경로 문제이거나 드라이버 문제인데, 화면에는
# 어느 쪽인지 남지 않는다. 이 환경변수를 켜면 stderr로 흘린다
# (tools/build_exe.py --console로 만든 exe와 함께 쓴다).
DEBUG_ENV = "LIKE_BOT_DEBUG"


def _debug(message: str) -> None:
    if os.environ.get(DEBUG_ENV):
        print(f"[browsers] {message}", file=sys.stderr, flush=True)


class BrowserInstallError(RuntimeError):
    """chromium 내려받기가 끝내 실패했다."""


def chromium_executable() -> Path:
    """지금 playwright가 요구하는 chromium 실행 파일 경로.

    설치 디렉터리를 `chromium-*`로 훑지 않는다. playwright는 버전마다 리비전을
    핀하므로 chromium-1228이 있어도 1234를 원하면 없는 것이고, 디렉터리 배치는
    playwright가 언제든 바꿀 수 있다 — 레거시 결함 2와 같은 종류의 추측이다.
    경로 계산은 playwright에게 맡긴다.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        return Path(pw.chromium.executable_path)
    finally:
        pw.stop()


def chromium_installed() -> bool:
    """설치돼 있으면 True.

    드라이버 기동 자체가 실패해도(번들이 깨졌거나 node를 못 띄웠거나) 예외를
    올리지 않는다. 여기서 죽으면 앱이 아예 뜨지 않는다 — 설치되지 않은 것으로
    보고 내려받기로 넘기면, 실패하더라도 운영자가 이유를 읽을 수 있는 화면에서
    실패한다.
    """
    _debug(f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH')!r}")
    try:
        exe = chromium_executable()
    except Exception as error:
        _debug(f"경로를 묻지 못했다: {error!r}")
        return False
    found = exe.exists()
    _debug(f"chromium={exe}  exists={found}")
    return found


def _driver_paths() -> tuple[str, str]:
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return str(node), str(cli)


def install_command() -> list[str]:
    return [*_driver_paths(), "install", "chromium"]


def _creation_flags() -> int:
    # --windowed로 빌드한 exe가 node를 그냥 띄우면 콘솔 창이 번쩍인다.
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _popen_driver() -> subprocess.Popen:
    return subprocess.Popen(
        install_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=_creation_flags(),
    )


def install_chromium(
    on_progress: OnLine | None = None,
    on_spawn: Callable[[subprocess.Popen], None] | None = None,
    *,
    popen: Callable[[], subprocess.Popen] = _popen_driver,
) -> None:
    """chromium을 내려받는다. 실패하면 마지막 출력과 함께 예외를 올린다.

    on_spawn은 뜨자마자의 프로세스를 넘겨준다 — 화면에서 취소를 누르면
    호출자가 이것으로 kill한다. 여기서 취소를 직접 다루지 않는 것은, 취소가
    UI의 사정이고 이 모듈은 CLI에서도 쓰이기 때문이다.
    """
    say = on_progress or (lambda _line: None)
    tail: list[str] = []

    process = popen()
    if on_spawn is not None:
        on_spawn(process)

    for line in process.stdout:
        text = line.rstrip()
        tail.append(text)
        del tail[:-ERROR_TAIL_LINES]
        say(text)

    code = process.wait()
    if code != 0:
        detail = "\n".join(tail) or "(출력 없음)"
        raise BrowserInstallError(
            f"chromium 내려받기가 실패했습니다 (종료코드 {code}).\n{detail}"
        )
