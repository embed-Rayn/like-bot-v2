"""배포 버전. 여기 한 곳만 고친다.

pyproject.toml(`[tool.setuptools.dynamic]`), 창 제목, exe 속성창의 버전이
전부 이 문자열에서 나온다. 받는 사람이 "몇 번 버전이냐"고 물었을 때
창 제목·파일 속성·저장소가 서로 다른 답을 하는 상황을 만들지 않기 위해서다.

Windows 버전 리소스는 숫자 네 칸(major, minor, patch, build)을 요구하므로
`version_tuple()`이 여기서 파생시킨다 — 빌드 칸은 0으로 고정한다.
"""
from __future__ import annotations

__version__ = "2.0.4"


def version_tuple() -> tuple[int, int, int, int]:
    parts = [int(p) for p in __version__.split(".")]
    parts += [0] * (4 - len(parts))
    return (parts[0], parts[1], parts[2], parts[3])
