# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 스펙 — onefile 데스크톱 exe.

`python tools/build_exe.py`로 부른다. 직접 `pyinstaller build.spec`도 된다.

chromium은 여기 들어가지 않는다. 압축 전 428MB짜리를 넣으면 exe만 그만큼
불어나고, 어차피 playwright가 버전별 리비전을 기본 위치에서 관리한다. 대신
playwright의 node 드라이버는 반드시 넣어야 한다 — exe에는 파이썬도 pip도
없어서 `python -m playwright install`을 부를 수 없고, 최초 실행 시
engine/browsers.py가 이 드라이버를 직접 실행해 chromium을 받는다.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

import playwright

# driver/에는 node.exe(92MB)와 package/(cli.js 포함)가 들어 있다. 파이썬
# 모듈이 아니라 데이터이므로 Analysis가 알아서 따라오지 않는다.
DRIVER = Path(playwright.__file__).parent / "driver"
datas = [(str(DRIVER), "playwright/driver")]
# _repo_version 등 playwright가 런타임에 읽는 데이터 파일.
datas += collect_data_files("playwright", excludes=["driver/*"])

hiddenimports = [
    # keyring은 백엔드를 런타임에 entry point로 찾는다 — 정적 분석에 안 걸린다.
    "keyring.backends.Windows",
    # pywin32의 오랜 지병: win32api가 이것을 런타임에만 import한다.
    "win32timezone",
    # sync API는 greenlet 위에서 돈다.
    "greenlet",
]

a = Analysis(
    ["desktop/app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 쓰지 않는 무거운 것들. 들어오면 exe만 커진다.
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt6.QtWebEngineCore"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="like-bot-v2-console" if os.environ.get("LIKE_BOT_CONSOLE") else "like-bot-v2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # node.exe에 UPX를 먹이면 실행이 깨지는 사례가 잦다
    # 기본은 --windowed(콘솔 없음). LIKE_BOT_CONSOLE=1로 빌드하면 콘솔이
    # 붙은 exe가 나온다 — 창이 안 뜨거나 조용히 죽을 때 stderr를 보려면
    # 그 방법뿐이다 (tools/build_exe.py --console).
    console=bool(os.environ.get("LIKE_BOT_CONSOLE")),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
