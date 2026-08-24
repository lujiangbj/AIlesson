"""内容完备度：某集某教学点能跑哪些教具、缺什么素材。

这是教研后台的核心视图。原先这个判断只存在于 friends_lesson.py 的一句注释
（「图和音都齐才收」），缺素材的后果要等上课时才发现。
"""
from __future__ import annotations

import pytest

from ailesson.content.completeness import assets_of, audit, item_status
from ailesson.contract.episode import load_episode


@pytest.fixture(scope="module")
def e01(mvp_root):
    return load_episode(mvp_root, "peppa-s01e01")


class TestAssets:
    def test_词的素材齐全(self, e01):
        have = assets_of(e01, "words", e01.words[0].lemma)
        assert "audio" in have
        assert "meaning_zh" in have

    def test_句子有原片切片(self, e01):
        have = assets_of(e01, "sentences", e01.sentences[0].id)
        assert "audio_clip" in have

    def test_未知域报错(self, e01):
        with pytest.raises(KeyError):
            assets_of(e01, "不存在的域", "x")

    def test_TTS冒充的原片不算原片(self, e01, monkeypatch):
        """Friends 的转换器把 audio_clip 填成了 audio_tts。

        字段非空但内容是合成音，只查非空会把「没有原片」这个缺口报成齐全 ——
        而那是接通老友记线的主要障碍，必须能在矩阵里看见。
        """
        s = e01.sentences[0]
        fake = type(s)(**{**s.__dict__, "audio_clip": s.audio_tts})
        monkeypatch.setattr(e01, "sentence", lambda _x, _f=fake: _f)
        assert "audio_clip" not in assets_of(e01, "sentences", s.id)


class TestItemStatus:
    def test_能跑的教具列出来(self, e01):
        st = item_status(e01, "words", e01.words[0].lemma)
        assert st.runnable
        assert st.label

    def test_缺素材的教具带原因(self, e01):
        """skip_image 的词跑不了听音选图，且要标成「有意跳过」而非缺失。"""
        skipped = [w for w in e01.words if w.skip_image]
        if not skipped:
            pytest.skip("这一集没有 skip_image 的词")
        st = item_status(e01, "words", skipped[0].lemma)
        assert "listen_pick_image" in st.blocked
        assert st.skip_image is True


@pytest.fixture(scope="module")
def rep(e01):
    return audit(e01)


class TestAudit:

    def test_三层都在(self, rep):
        assert set(rep["domains"]) == {"words", "chunks", "sentences"}

    def test_每层给出总数和可教数(self, rep):
        for dom in rep["domains"].values():
            assert dom["total"] >= 0
            assert dom["ready"] <= dom["total"]

    def test_列出教具作为矩阵的列(self, rep):
        assert rep["tools"]
        assert all("id" in t and "name" in t for t in rep["tools"])

    def test_按教具统计通过数(self, rep):
        by_tool = rep["domains"]["words"]["by_tool"]
        assert "listen_pick_image" in by_tool
        assert "ok" in by_tool["listen_pick_image"]

    def test_缺口按数量排序(self, rep):
        counts = [b["count"] for b in rep["blockers"]]
        assert counts == sorted(counts, reverse=True)

    def test_peppa素材基本齐全(self, rep):
        """Peppa 这一集是完整的，词层不该有大面积缺口。"""
        w = rep["domains"]["words"]
        assert w["ready"] > w["total"] * 0.5

    def test_可以只审指定教具(self, e01):
        rep = audit(e01, tool_ids=["shadow"])
        assert [t["id"] for t in rep["tools"]] == ["shadow"]
