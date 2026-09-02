# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 스펙.

    python -m PyInstaller --noconfirm packaging/like-bot-v2.spec

onefile이 아니라 onedir이다. playwright 드라이버(node.exe + cli.js)와 PyQt6
플러그인을 onefile로 묶으면 실행할 때마다 임시 폴더에 수십 MB를 풀어야 해서
기동이 느려지고 백신 오탐도 잦다. dist/like-bot-v2/ 폴더째 압축해 배포한다.

chromium은 이 PC의 playwright 캐시에서 통째로 실어 보낸다 — 받는 PC는 인터넷 없이
바로 쓸 수 있다. 캐시에 chromium이 없으면(= `playwright install chromium` 전) 브라우저
없이 빌드되고, 그 배포본은 처음 실행할 때 %LOCALAPPDATA%\\like-bot-v2\\browsers 로
직접 내려받는다. 어느 쪽이든 engine/browsers.py가 알아서 고른다.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_entry_point

ROOT = Path(SPECPATH).parent


def _playwright_cache() -> Path:
    """이 PC가 브라우저를 받아 둔 곳."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    return Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"


def _browsers_to_ship() -> list[tuple[str, str]]:
    """실어 보낼 브라우저 폴더 목록. 없으면 빈 목록.

    chromium(헤드풀)과 winldd만 싣는다. chromium_headless_shell은 272MB나 되면서
    이 앱에는 쓸모가 없다 — engine/session.py는 언제나 headless=False로 띄우고,
    로그인 추가 확인은 사람이 창을 봐야 끝낼 수 있다.
    """
    cache = _playwright_cache()
    if not cache.is_dir():
        return []
    shipped = []
    for prefix in ("chromium-", "winldd-"):
        found = sorted(
            (d for d in cache.iterdir() if d.is_dir() and d.name.startswith(prefix)),
            key=lambda d: d.name,
        )
        if found:
            newest = found[-1]
            shipped.append((str(newest), f"ms-playwright/{newest.name}"))
    return shipped

# playwright는 순수 파이썬 패키지가 아니다. driver/ 아래에 node.exe와 cli.js가
# 들어 있고, 이걸 빼먹으면 배포본이 "Executable doesn't exist"로 죽는다.
# pyinstaller-hooks-contrib에 playwright 훅이 없으므로(6.22 기준 확인) 직접 모은다.
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

# keyring은 백엔드를 진입점(entry point)으로 늦게 찾는다. 정적 분석으로는
# 보이지 않아서 배포본에서만 "No recommended backend"로 실패한다.
kr_datas, kr_hiddenimports = collect_entry_point("keyring.backends")

a = Analysis(
    [str(ROOT / "desktop" / "app.py")],
    pathex=[str(ROOT)],
    binaries=pw_binaries,
    datas=pw_datas + kr_datas + _browsers_to_ship(),
    hiddenimports=[
        *pw_hiddenimports,
        *kr_hiddenimports,
        "keyring.backends.Windows",
        "win32timezone",  # pywin32가 런타임에만 import 한다
    ],
    hookspath=[],
    excludes=[
        # 테스트 전용. 배포본에 들어갈 이유가 없다.
        "pytest",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="like-bot-v2",
    debug=False,
    strip=False,
    upx=False,  # UPX는 백신 오탐의 주된 원인이다. 크기를 줄이려 켜지 말 것.
    # 콘솔 창을 띄우지 않는다. 대신 잡히지 않은 예외는 bootstrap()이
    # logs/crash.log에 남기고 대화상자로 알린다 — desktop/app.py.
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="like-bot-v2",
)
