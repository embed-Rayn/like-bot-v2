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
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent


def _version() -> tuple[str, tuple[int, int, int, int]]:
    """engine/version.py에서 버전을 읽는다.

    import 하지 않고 읽는 이유: 스펙은 PyInstaller가 exec 하는 스크립트라
    ROOT가 sys.path에 없고, engine을 import 하면 빌드 대상 패키지를 빌드
    스크립트가 먼저 끌어들이게 된다. 문자열 하나를 위해 치를 값이 아니다.
    """
    text = (ROOT / "engine" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("engine/version.py에서 __version__을 찾지 못했다")
    dotted = m.group(1)
    parts = [int(x) for x in dotted.split(".")] + [0, 0, 0, 0]
    return dotted, tuple(parts[:4])


VERSION, VERSION_TUPLE = _version()

# exe 속성창(자세히 탭)에 버전을 박는다. 배포본은 폴더째 압축해서 돌아다니고
# 압축 파일 이름은 쉽게 바뀌므로, 받은 사람이 "이게 몇 번이냐"를 확인할 수
# 있는 곳은 사실상 파일 속성뿐이다. 창 제목도 같은 값을 쓴다 (desktop/app.py).
VERSION_RESOURCE = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
    kids=[
        StringFileInfo([
            StringTable("041204B0", [  # 한국어(ko-KR) · 유니코드
                StringStruct("CompanyName", "delfino"),
                StringStruct("FileDescription", "blog search & like"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "like-bot-v2"),
                StringStruct("OriginalFilename", "like-bot-v2.exe"),
                StringStruct("ProductName", "like-bot-v2"),
                StringStruct("ProductVersion", VERSION),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [0x0412, 1200])]),
    ],
)


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

# keyring 훅은 2026-09-11에 빠졌다. 앱이 자격증명을 저장하지 않으므로
# (자동 로그인 폐지 — engine/session.py의 _login) keyring 자체를 쓰지 않는다.

a = Analysis(
    [str(ROOT / "desktop" / "app.py")],
    pathex=[str(ROOT)],
    binaries=pw_binaries,
    datas=pw_datas + _browsers_to_ship(),
    hiddenimports=[
        *pw_hiddenimports,
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
    version=VERSION_RESOURCE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="like-bot-v2",
)
