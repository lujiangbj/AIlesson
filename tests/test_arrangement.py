"""编排：16 环节从代码变成带版本号的配置。

最要紧的一条是版本兼容 —— 编排可编辑之后，旧快照拿新编排去重建会得到另一副牌
（§10.5 那个坑的升级版，而且这次不会报错，只会静默错位）。
"""
from __future__ import annotations

import pytest

from ailesson.classroom.arrangement import (
    DEFAULT,
    Arrangement,
    Step,
    compatible,
)
from ailesson.contract.tools import TOOLS


class TestDefault:
    def test_默认编排是16环节(self):
        assert len(DEFAULT.steps) == 16

    def test_序号从1连续到16(self):
        assert [s.index for s in DEFAULT.steps] == list(range(1, 17))

    def test_有版本号和id(self):
        assert DEFAULT.id
        assert DEFAULT.version >= 1

    def test_每个环节都有中文标题(self):
        for s in DEFAULT.steps:
            assert s.title, s.index

    def test_引用的教具都存在(self):
        for s in DEFAULT.steps:
            for tid in s.tool_ids():
                assert tid in TOOLS, f"环节 {s.index} 引用了不存在的教具 {tid}"

    def test_总时长约32分钟(self):
        # PRD FR-4：一节课 32 分钟
        assert 30 <= DEFAULT.minutes() <= 34

    def test_最后一环是报告(self):
        assert DEFAULT.steps[-1].tool == "report"

    def test_计分环节与PRD一致(self):
        """§10.7：只有首触和反向计 streak，跟读/混打/重做是巩固。"""
        assert DEFAULT.scored_indexes() == {1, 2, 3, 4, 6, 7, 10, 11}

    def test_首触环节是3_6_10(self):
        assert DEFAULT.first_touch_indexes() == {3, 6, 10}

    def test_按序号取环节(self):
        assert DEFAULT.step(6).domains == ("chunks",)
        with pytest.raises(KeyError):
            DEFAULT.step(99)


class TestValidate:
    def test_序号不连续要报错(self):
        steps = [s for s in DEFAULT.steps if s.index != 5]
        with pytest.raises(ValueError, match="连续"):
            Arrangement(id="x", version=1, steps=tuple(steps)).validate()

    def test_教具不存在要报错(self):
        bad = Step(index=1, key="x", title="坏环节", minutes=1.0,
                   source="focus", domains=("words",), tool="不存在的教具")
        with pytest.raises(ValueError, match="教具"):
            Arrangement(id="x", version=1, steps=(bad,)).validate()

    def test_教具与域不匹配要报错(self):
        """听音选图只适用于词。配到句子上是编排错误，不能到运行时才炸。"""
        bad = Step(index=1, key="x", title="坏环节", minutes=1.0,
                   source="focus", domains=("sentences",),
                   tool="listen_pick_image")
        with pytest.raises(ValueError, match="域"):
            Arrangement(id="x", version=1, steps=(bad,)).validate()

    def test_非答题教具不许标计分(self):
        bad = Step(index=1, key="x", title="坏环节", minutes=1.0,
                   source="focus", domains=("sentences",), tool="watch_clip",
                   scored=True)
        with pytest.raises(ValueError, match="计分"):
            Arrangement(id="x", version=1, steps=(bad,)).validate()

    def test_默认编排自检通过(self):
        DEFAULT.validate()          # 不抛就算过


class TestSnapshotCompat:
    """编排改了 = 换教材。旧快照不许拿新编排重建。"""

    def test_同id同版本兼容(self):
        assert compatible(DEFAULT, {"arrangement_id": DEFAULT.id,
                                    "arrangement_version": DEFAULT.version})

    def test_版本不同不兼容(self):
        assert not compatible(DEFAULT, {"arrangement_id": DEFAULT.id,
                                        "arrangement_version": DEFAULT.version + 1})

    def test_id不同不兼容(self):
        assert not compatible(DEFAULT, {"arrangement_id": "别的编排",
                                        "arrangement_version": DEFAULT.version})

    def test_老快照没记编排就按默认算兼容(self):
        """v0.6 之前的快照没有这两个字段，它们本来就是跑在默认编排上的。"""
        assert compatible(DEFAULT, {})
        assert not compatible(
            Arrangement(id="别的", version=1, steps=DEFAULT.steps), {}
        )

    def test_stamp_写进快照的字段(self):
        assert DEFAULT.stamp() == {"arrangement_id": DEFAULT.id,
                                   "arrangement_version": DEFAULT.version}


class TestSerialize:
    def test_to_dict_能给后台展示(self):
        d = DEFAULT.to_dict()
        assert d["id"] == DEFAULT.id
        assert len(d["steps"]) == 16
        # 后台要显示教具中文名，不能只给 id
        assert d["steps"][2]["tool_name"] == "听音选图"

    def test_每个环节都带素材需求(self):
        """教研后台的完备度矩阵要按环节看缺什么。"""
        for s in DEFAULT.to_dict()["steps"]:
            assert "needs" in s
