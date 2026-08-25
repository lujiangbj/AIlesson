"""切段结果落盘：`segment_episode()` 的结果要能存下来给下游用。

原先只打印在终端里 —— 下一步（选词/选句/配图）拿不到段边界，只能整集处理，
「一集 = 多段，每段 = N 节课」这层结构在数据里根本不存在。
"""
from __future__ import annotations

import json

import pytest

from ailesson.content.segment import (
    SegmentPlan,
    load_plan,
    save_plan,
    segment_episode,
)


@pytest.fixture(scope="module")
def items():
    """一集的 items：3 个换场，词数刻意不均。"""
    out = []
    for si, (scene, n_lines, wlen) in enumerate([
        ("Central Perk, everyone is there", 12, 10),
        ("Monica's Apartment, later", 8, 6),
        ("The Museum, Ross at work", 6, 12),
    ]):
        out.append({"type": "scene", "text": scene})
        for i in range(n_lines):
            out.append({"type": "line", "speaker": f"P{si}",
                        "text": " ".join(["word"] * wlen)})
    return out


class TestPlan:
    def test_按段数切并给出计划(self, items):
        plan = SegmentPlan.build("0101", items, n=3)
        assert plan.episode_id == "0101"
        assert len(plan.segments) == 3
        assert plan.n == 3

    def test_计划带不均衡度(self, items):
        plan = SegmentPlan.build("0101", items, n=3)
        assert plan.spread >= 1.0

    def test_每段带地点句数词数(self, items):
        for s in SegmentPlan.build("0101", items, n=3).segments:
            assert s["locations"]
            assert s["lines"] > 0
            assert s["words"] > 0

    def test_每段带首末句_便于核对切点(self, items):
        """看拆分效果最直接的就是「这段从哪句起、到哪句止」。"""
        for s in SegmentPlan.build("0101", items, n=3).segments:
            assert s["first_line"]["text"]
            assert s["last_line"]["text"]

    def test_每段带估算时长(self, items):
        plan = SegmentPlan.build("0101", items, n=3, runtime_min=24)
        assert abs(sum(s["minutes"] for s in plan.segments) - 24) < 0.1

    def test_段号从1连续(self, items):
        plan = SegmentPlan.build("0101", items, n=3)
        assert [s["index"] for s in plan.segments] == [1, 2, 3]

    def test_不给段数就自动选(self, items):
        plan = SegmentPlan.build("0101", items)
        assert 1 <= plan.n <= 6
        assert plan.auto is True

    def test_给了段数就不算自动(self, items):
        assert SegmentPlan.build("0101", items, n=4).auto is False

    def test_记下切段规则_便于复现(self, items):
        """规则会变（现在是词数均分 + 吸附换场）。存下来才知道这份是怎么切的。"""
        plan = SegmentPlan.build("0101", items, n=3)
        assert plan.rule
        assert "换场" in plan.rule or "scene" in plan.rule


class TestRoundTrip:
    def test_存了能读回来(self, items, tmp_path):
        plan = SegmentPlan.build("0101", items, n=3)
        p = save_plan(plan, tmp_path)
        assert p.exists()
        back = load_plan("0101", tmp_path)
        assert back is not None
        assert back.to_dict() == plan.to_dict()

    def test_落盘是可读的json(self, items, tmp_path):
        save_plan(SegmentPlan.build("0101", items, n=3), tmp_path)
        raw = json.loads((tmp_path / "0101.json").read_text())
        assert raw["episode_id"] == "0101"
        assert len(raw["segments"]) == 3

    def test_没存过就读到None(self, tmp_path):
        assert load_plan("9999", tmp_path) is None

    def test_坏文件不炸(self, tmp_path):
        (tmp_path / "0101.json").write_text("{ 坏的")
        assert load_plan("0101", tmp_path) is None


class TestVocab:
    def test_可以附生词量(self, items):
        """每段几个生词，直接决定这段能出几节课。"""
        levels = {"word": "B1"}
        plan = SegmentPlan.build("0101", items, n=3, levels=levels)
        for s in plan.segments:
            assert s["new_words"] >= 1
            assert s["est_lessons"] > 0

    def test_不给词表时不报生词(self, items):
        for s in SegmentPlan.build("0101", items, n=3).segments:
            assert s["new_words"] is None


class TestStageBoundary:
    """允许拿舞台提示当次级边界。

    Friends S1E1 的开场 Central Perk 一场 107 句 1469 词，占全集 39%。只按换场
    切的话它是不可分的整体，任何包含它的段都至少这么大 —— 段数越多其它段越碎。
    那场戏内部有 9 个舞台提示（Time Lapse / 人物进出），拿它们当次级边界能切开。
    """

    @pytest.fixture
    def one_big_scene(self):
        """一个大场景（内含 2 个舞台提示）+ 两个小场景。"""
        out = [{"type": "scene", "text": "Central Perk"}]
        for i in range(10):
            out.append({"type": "line", "speaker": "A",
                        "text": " ".join(["word"] * 10)})
        out.append({"type": "stage", "text": "Time Lapse"})
        for i in range(10):
            out.append({"type": "line", "speaker": "B",
                        "text": " ".join(["word"] * 10)})
        out.append({"type": "stage", "text": "Ross enters"})
        for i in range(10):
            out.append({"type": "line", "speaker": "C",
                        "text": " ".join(["word"] * 10)})
        for scene in ("Monica's Apartment", "The Museum"):
            out.append({"type": "scene", "text": scene})
            for i in range(4):
                out.append({"type": "line", "speaker": "D",
                            "text": " ".join(["word"] * 5)})
        return out

    def test_默认只按换场切(self, one_big_scene):
        plan = SegmentPlan.build("x", one_big_scene, n=3)
        # 大场景整块进第 1 段
        assert plan.segments[0]["words"] == 300

    def test_开启后能切开大场景(self, one_big_scene):
        plan = SegmentPlan.build("x", one_big_scene, n=3, use_stage=True)
        assert plan.segments[0]["words"] < 300

    def test_开启后更均(self, one_big_scene):
        a = SegmentPlan.build("x", one_big_scene, n=3)
        b = SegmentPlan.build("x", one_big_scene, n=3, use_stage=True)
        assert b.spread < a.spread

    def test_规则说明里写明用了次级边界(self, one_big_scene):
        plan = SegmentPlan.build("x", one_big_scene, n=3, use_stage=True)
        assert "舞台提示" in plan.rule

    def test_仍然不丢句子(self, one_big_scene):
        plan = SegmentPlan.build("x", one_big_scene, n=3, use_stage=True)
        assert sum(s["lines"] for s in plan.segments) == plan.total_lines

    def test_段内地点仍可追溯(self, one_big_scene):
        """在场景内部切开后，两半都还属于同一个地点。"""
        plan = SegmentPlan.build("x", one_big_scene, n=3, use_stage=True)
        assert any("Central Perk" in s["locations"] for s in plan.segments)


class TestConsistency:
    def test_切段不丢句子(self, items):
        plan = SegmentPlan.build("0101", items, n=3)
        assert sum(s["lines"] for s in plan.segments) == plan.total_lines

    def test_切段不丢词(self, items):
        plan = SegmentPlan.build("0101", items, n=3)
        assert sum(s["words"] for s in plan.segments) == plan.total_words

    def test_段数超过换场数时收敛(self, items):
        """3 个换场要不了 10 段。不能崩，也不能出空段。"""
        plan = SegmentPlan.build("0101", items, n=10)
        assert plan.n <= 3
        assert all(s["lines"] > 0 for s in plan.segments)
