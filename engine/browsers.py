"""배포본이 쓸 chromium을 찾고, 없으면 받아온다.

exe 안에는 파이썬이 없다. `sys.executable -m playwright install`은 개발 환경에서만
동작하므로, playwright가 자기 드라이버(node.exe + cli.js) 경로를 알려주는 공식
진입점을 통해 같은 명령을 직접 부른다.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

from engine.paths import AppPaths

ENV_VAR = "PLAYWRIGHT_BROWSERS_PATH"


class BrowserInstallError(RuntimeError):
    """chromium 내려받기가 실패했다."""


def apply_browsers_env(paths: AppPaths) -> None:
    """배포본일 때만 브라우저 위치를 앱 폴더로 고정한다.

    개발 실행에서 이걸 건드리면 이미 받아둔 ms-playwright 캐시를 못 쓰게 되고,
    배포본이 그 캐시를 쓰면 "내 PC에선 되는데"가 구조적으로 발생한다. 운영자가
    직접 지정한 값은 어느 쪽이든 존중한다 — 그러지 않으면 경로를 바꿔 볼 방법이
    없어 진단이 막힌다.
    """
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get(ENV_VAR):
        return
    os.environ[ENV_VAR] = str(paths.browsers_dir)


def chromium_present(root: Path) -> bool:
    """창을 띄울 수 있는 chromium이 있는가.

    헤드리스 셸(chromium_headless_shell-*)은 세지 않는다. 로그인은 사람이 창을
    보고 끝내야 하므로 그것만 있으면 아무 소용이 없다.
    """
    root = Path(root)
    if not root.is_dir():
        return False
    return any(
        child.is_dir() and child.name.startswith("chromium-") for child in root.iterdir()
    )


def driver_install_command() -> list[str]:
    """playwright 드라이버로 chromium을 받는 명령."""
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    return [str(node), str(cli), "install", "chromium"]


async def install_chromium(
    on_line: Callable[[str], None],
    *,
    command: list[str] | None = None,
) -> None:
    """chromium을 내려받으며 출력을 한 줄씩 흘린다.

    진행률을 뽑아내지 않는다. 남의 출력 형식을 파싱하면 그쪽이 바뀔 때 조용히
    깨진다 — 네이버 마크업에서 이미 겪은 실패다. 줄을 그대로 보여준다.
    """
    command = command or driver_install_command()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    async for raw in process.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            on_line(line)
    if await process.wait() != 0:
        raise BrowserInstallError(
            f"chromium 설치가 실패했습니다 (종료 코드 {process.returncode})."
        )


def managed_root() -> Path | None:
    """우리가 관리하는 브라우저 폴더. 배포본이 아니면 None.

    개발 실행에서 playwright 기본 캐시 경로를 추측하지 않는다. 그 경로는 OS와
    playwright 버전에 따라 다르고, 틀리면 이미 받아둔 브라우저를 또 받는다.
    """
    value = os.environ.get(ENV_VAR)
    return Path(value) if value else None


async def ensure_chromium(
    on_line: Callable[[str], None],
    *,
    installer: Callable[[Callable[[str], None]], object] = install_chromium,
) -> None:
    """실행 직전에 창을 띄울 chromium이 있는지 보고, 없으면 받는다."""
    root = managed_root()
    if root is None or chromium_present(root):
        return
    on_line(f"브라우저를 처음 한 번 내려받습니다 (약 450MB) — {root}")
    await installer(on_line)
    on_line("브라우저 준비 완료.")
