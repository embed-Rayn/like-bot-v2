"""exe 빌드 — `python tools/build_exe.py`.

빌드는 프로젝트 venv에서 해야 한다. conda 환경에서 빌드하면 PyInstaller가
환경 전체의 잡동사니까지 훑어 exe가 몇 배로 커진다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "build.spec"


def main(argv: list[str]) -> int:
    # --console: 콘솔이 붙은 exe. 창이 안 뜨거나 조용히 죽을 때
    # stderr를 볼 유일한 방법이다. 배포용은 아니다.
    console = "--console" in argv
    env = dict(os.environ)
    if console:
        env["LIKE_BOT_CONSOLE"] = "1"
    else:
        env.pop("LIKE_BOT_CONSOLE", None)
    name = "like-bot-v2-console" if console else "like-bot-v2"
    dist = ROOT / "dist" / f"{name}.exe"

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller가 없습니다:  pip install pyinstaller", file=sys.stderr)
        return 1

    # --clean 없이 다시 빌드하면 예전 분석 결과가 남아, 스펙을 고쳐도 반영되지
    # 않은 exe가 나온다.
    for stale in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(stale, ignore_errors=True)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"],
        cwd=ROOT,
        env=env,
    )
    if result.returncode != 0:
        return result.returncode

    if not dist.exists():
        print(f"빌드는 끝났는데 {dist}가 없습니다.", file=sys.stderr)
        return 1

    print(f"\n완료: {dist}  ({dist.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
