"""教具表：把散在 lesson3 里的卡型收成可枚举、可声明素材需求的一等对象。"""
from __future__ import annotations

import pytest

from ailesson.contract.tools import (
    INTERACTIONS,
    TOOLS,
    Tool,
    missing_assets,
    tool,
    tool_for,
    tools_for_domain,
)


class TestRegistry:
    def test_每个教具都有中文名和交互形态(self):
        assert TOOLS, "教具表不能是空的"
        for t in TOOLS.values():
            assert t.name, f"{t.id} 缺中文名"
            assert t.interaction in INTERACTIONS, f"{t.id} 交互形态非法"

    def test_id_与键一致(self):
        for key, t in TOOLS.items():
            assert key == t.id

    def test_tool_查不到就报错(self):
        with pytest.raises(KeyError):
            tool("不存在的教具")

    def test_答题类教具必须计分_跟读被动类不计(self):
        for t in TOOLS.values():
            if t.interaction == "quiz":
                assert t.scored, f"{t.id} 是答题却不计分"
            else:
                assert not t.scored, f"{t.id} 不是答题却计分"

    def test_答题类教具都要声明方向(self):
        for t in TOOLS.values():
            if t.interaction == "quiz":
                assert t.direction in ("a2i", "i2a"), t.id
            else:
                assert t.direction == "none", t.id


class TestDomains:
    def test_每个域至少有一个可用教具(self):
        for dom in ("words", "chunks", "sentences"):
            assert tools_for_domain(dom), dom

    def test_听音选图只给词_短语句子走选义(self):
        # 词的选项是图；短语句子图区分度不够，选项必须是文字释义
        assert tools_for_domain("words")
        assert "listen_pick_image" in {t.id for t in tools_for_domain("words")}
        for dom in ("chunks", "sentences"):
            ids = {t.id for t in tools_for_domain(dom)}
            assert "listen_pick_image" not in ids
            assert "listen_pick_meaning" in ids

    def test_tool_for_按方向和域定位教具(self):
        assert tool_for("a2i", "words").id == "listen_pick_image"
        assert tool_for("a2i", "chunks").id == "listen_pick_meaning"
        assert tool_for("i2a", "words").id == "recall_pick_audio"
        assert tool_for("i2a", "sentences").id == "recall_pick_audio"

    def test_tool_for_域不支持时报错(self):
        with pytest.raises(KeyError):
            tool_for("a2i", "不存在的域")


class TestAssets:
    """素材需求是教具表的核心用途：内容完备度矩阵靠它算。"""

    def test_每个教具都声明素材需求(self):
        for t in TOOLS.values():
            assert isinstance(t.needs, tuple)

    def test_听音选图要音频和图(self):
        t = tool("listen_pick_image")
        assert "audio" in t.needs and "image" in t.needs

    def test_听音选义不要图(self):
        assert "image" not in tool("listen_pick_meaning").needs

    def test_句子原声要原片切片(self):
        assert "audio_clip" in tool("watch_clip").needs

    def test_missing_assets_报出缺哪些(self):
        assert missing_assets(tool("listen_pick_image"), {"audio"}) == ("image",)
        assert missing_assets(tool("listen_pick_image"),
                              {"audio", "image"}) == ()

    def test_missing_assets_按声明顺序返回(self):
        t = Tool(id="x", name="x", interaction="quiz", direction="a2i",
                 domains=("words",), needs=("audio", "image", "meaning_zh"))
        assert missing_assets(t, set()) == ("audio", "image", "meaning_zh")
