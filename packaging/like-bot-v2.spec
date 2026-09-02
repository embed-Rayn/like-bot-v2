# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 스펙.

    python -m PyInstaller --noconfirm packaging/like-bot-v2.spec

onefile이 아니라 onedir이다. playwright 드라이버(node.exe + cli.js)와 PyQt6
플러그인을 onefile로 묶으면 실행할 때마다 임시 폴더에 수십 MB를 풀어야 해서
기동이 느려지고 백신 오탐도 잦다. dist/like-bot-v2/ 폴더째 압축해 배포한다.

chromium은 넣지 않는다 (약 450MB). 배포본은 처음 실행할 때
%LOCALAPPDATA%\\like-bot-v2\\browsers 로 직접 내려받는다 — engine/browsers.py.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_entry_point

ROOT = Path(SPECPATH).parent

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
    datas=pw_datas + kr_datas,
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
