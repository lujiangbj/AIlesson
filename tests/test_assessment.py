"""三层自评测试（词 / 短语 / 句子各自分池）。"""
import json

import pytest

from ailesson.assessment import SelfAssessment, build_assessment
from ailesson.episode import load_episode


@pytest.fixture(scope="module")
def e01(mvp_root):
    return load_episode(mvp_root, "peppa-s01e01")


@pytest.fixture
def all_items(e01):
    return {
        "words": [w.lemma for w in e01.words],
        "chunks": [c.id for c in e01.chunks],
        "sentences": [s.id for s in e01.sentences],
    }


class TestBuild:
    def test_三层各自分池(self, all_items):
        a = build_assessment("e", all_items, {
            "words": ["peppa", "pig"],
            "chunks": ["im_peppa"],
            "sentences": ["s01"],
        })
        # 池内顺序跟素材一致（words 按词频降序），不跟勾选顺序
        assert set(a.known_words) == {"peppa", "pig"}
        assert a.known_chunks == ["im_peppa"]
        assert a.known_sentences == ["s01"]
        assert len(a.unknown_words) == 51
        assert len(a.unknown_chunks) == 27
        assert len(a.unknown_sentences) == 15

    def test_全不勾则全部要学(self, all_items):
        a = build_assessment("e", all_items, {})
        assert a.total_unknown() == 53 + 28 + 16

    def test_全勾则无需上课(self, all_items):
        a = build_assessment("e", all_items, all_items)
        assert a.total_unknown() == 0

    def test_忽略不存在的条目(self, all_items):
        a = build_assessment("e", all_items, {"words": ["peppa", "zzz"]})
        assert "zzz" not in a.known_words
        assert "zzz" not in a.unknown_words

    def test_CET6场景_词几乎全会但短语句子不会(self, all_items):
        """这是驱动本次改造的真实场景。"""
        a = build_assessment("e", all_items, {
            "words": [w for w in all_items["words"]
                      if w not in ("puddle", "muddy", "mud", "boot", "goodness")],
            "chunks": ["im_peppa", "daddy_daddy"],     # 只有最简单的会
            "sentences": [],                            # 句子一句都说不出
        })
        assert len(a.unknown_words) == 5
        # 词层只剩 5 个，但教学点总数仍然可观 —— 这才是他要学的
        assert a.total_unknown() == 5 + 26 + 16
        assert a.total_unknown() > 40

    def test_保持原始顺序(self, all_items):
        """不会池要保持剧情顺序，打包依赖它。"""
        a = build_assessment("e", all_items, {"sentences": ["s05"]})
        assert a.unknown_sentences[:3] == ["s01", "s02", "s03"]


class TestDemote:
    def test_三层都能打回(self, all_items):
        a = build_assessment("e", all_items, {
            "words": ["peppa"], "chunks": ["im_peppa"], "sentences": ["s01"],
        })
        a.demote("words", "peppa")
        a.demote("chunks", "im_peppa")
        a.demote("sentences", "s01")
        assert "peppa" in a.unknown_words
        assert "im_peppa" in a.unknown_chunks
        assert "s01" in a.unknown_sentences
        assert a.how["words"]["peppa"] == "demoted"

    def test_重复打回不出错(self, all_items):
        a = build_assessment("e", all_items, {"words": ["peppa"]})
        a.demote("words", "peppa")
        a.demote("words", "peppa")
        assert a.unknown_words.count("peppa") == 1

    def test_打回不在会池的是无操作(self, all_items):
        a = build_assessment("e", all_items, {})
        before = list(a.unknown_words)
        a.demote("words", "peppa")
        assert a.unknown_words == before


class TestPersistence:
    def test_往返(self, all_items):
        a = build_assessment("e", all_items, {
            "words": ["peppa"], "chunks": ["im_peppa"], "sentences": ["s01"],
        })
        back = SelfAssessment.from_dict(json.loads(json.dumps(a.to_dict())))
        assert back == a

    def test_空结构可读(self):
        a = SelfAssessment.from_dict({"episode_id": "e"})
        assert a.total_unknown() == 0
        assert a.known_words == []
