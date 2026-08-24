"""共享 fixture。

真实素材来自 illit-english-mvp（只读，不修改）。
"""
import json
import os
from pathlib import Path

import pytest

MVP_ROOT = Path(
    os.environ.get(
        "AILESSON_MVP_ROOT",
        "~/Claude/nowordenglish/illit-english-mvp",
    )
).expanduser()


@pytest.fixture(scope="session")
def mvp_root() -> Path:
    if not MVP_ROOT.exists():
        pytest.skip(f"素材目录不存在: {MVP_ROOT}")
    return MVP_ROOT


@pytest.fixture(scope="session")
def e01_raw(mvp_root: Path) -> dict:
    """E01 原始 lesson JSON。"""
    return json.loads((mvp_root / "lesson-peppa-s01e01.json").read_text())
