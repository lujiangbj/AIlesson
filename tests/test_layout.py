"""包边界守卫。

三块要真能分开迭代，靠的是依赖单向，不是目录看起来分开了。
这里把方向钉死，漏回去就红。

    contract ← 谁都能读，它谁都不读
    content  → 只能读 contract / infra      （教研不许知道学习者的存在）
    course   → contract / infra / learner    （组课不许读教室端）
    classroom→ contract / infra / learner    （教室端不许读组课和教研）
    learner  → 只能读 contract / infra
    infra    → 谁都不读
    session / server → 编排层，可以读任何一块
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "ailesson"

# 每个包允许依赖的包（不含自己）
ALLOWED = {
    "contract": set(),
    "infra": set(),
    "content": {"contract", "infra"},
    "learner": {"contract", "infra"},
    "course": {"contract", "infra", "learner"},
    "classroom": {"contract", "infra", "learner"},
}


def _imports(path: Path) -> set[str]:
    """这个文件 import 了 ailesson 的哪些子包。"""
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("ailesson."):
                    out.add(a.name.split(".")[1])
            continue
        if mod and mod.startswith("ailesson."):
            out.add(mod.split(".")[1])
    return out


def _files(pkg: str) -> list[Path]:
    return sorted((SRC / pkg).rglob("*.py"))


class TestDependencyDirection:
    @pytest.mark.parametrize("pkg", sorted(ALLOWED))
    def test_包只依赖允许的包(self, pkg: str) -> None:
        allowed = ALLOWED[pkg] | {pkg}
        for f in _files(pkg):
            bad = _imports(f) - allowed
            assert not bad, (
                f"{f.relative_to(SRC)} 不该依赖 {sorted(bad)}；"
                f"{pkg} 只允许依赖 {sorted(ALLOWED[pkg]) or '（无）'}"
            )

    def test_契约层不依赖任何内部包(self) -> None:
        for f in _files("contract"):
            assert _imports(f) <= {"contract"}, f

    def test_教研线不知道学习者的存在(self) -> None:
        """内容生产是可复用产物，一旦读了学习者数据就没法预生成。"""
        for f in _files("content"):
            assert "learner" not in _imports(f), f

    def test_教室端不依赖组课(self) -> None:
        """教室端只吃课程契约（LessonSpec），不关心它怎么被算出来的。"""
        for f in _files("classroom"):
            assert "course" not in _imports(f), f


class TestPaths:
    def test_ROOT_指向项目根(self) -> None:
        """state.py 在 server/ 下，parents[] 层数容易数错，这里钉死。"""
        from ailesson.server.state import ROOT

        assert (ROOT / "pyproject.toml").exists(), f"ROOT 错了：{ROOT}"
        assert (ROOT / "web").is_dir(), f"ROOT 错了：{ROOT}"

    def test_包目录都有说明(self) -> None:
        for pkg in [*ALLOWED, "server"]:
            init = SRC / pkg / "__init__.py"
            assert init.exists(), pkg
            assert init.read_text().strip().startswith('"""'), f"{pkg} 缺包说明"
