"""词汇 CEFR 归一化测试。

重点覆盖机械匹配的已知盲区：goodnight → good night(A1)、alright → all right(A1)。
LLM 那一层用 FakeLLM 打桩，不联网。
"""
import json

import pytest

from ailesson.infra.llm import FakeLLM
from ailesson.content.vocab_cefr import (
    VocabEntry,
    build_profile,
    candidates,
    llm_normalize,
    lookup,
    mechanical_pass,
    token_freq,
)


@pytest.fixture
def wordlist() -> dict[str, str]:
    """迷你词表，含词组条目——正是机械匹配会漏的那类。"""
    return {
        "good night": "A1",
        "all right": "A1",
        "be": "A1",
        "have": "A1",
        "move": "A1",
        "card": "A1",
        "wedding": "A2",
        "decaf": "B2",
        "kind of": "A2",
        "out of": "A2",
    }


class TestTokenFreq:
    def test_缩略形式算一个词(self):
        freq = token_freq(["I'm fine, don't worry"])
        assert freq["i'm"] == 1
        assert freq["don't"] == 1

    def test_大小写合并(self):
        assert token_freq(["Hi hi HI"])["hi"] == 3

    def test_忽略标点和数字(self):
        freq = token_freq(["Room 101 -- nice!"])
        assert set(freq) == {"room", "nice"}


class TestCandidates:
    def test_动词还原(self):
        assert "move" in candidates("moved")

    def test_复数还原(self):
        assert "card" in candidates("cards")

    def test_所有格剥离(self):
        """monica's → monica，之前这条逻辑没生效。"""
        assert "monica" in candidates("monica's")

    def test_缩略映射到本体(self):
        assert "be" in candidates("i'm")


class TestLookup:
    def test_直接命中(self, wordlist):
        assert lookup("wedding", wordlist) == ("wedding", "A2")

    def test_变形后命中(self, wordlist):
        assert lookup("moved", wordlist) == ("move", "A1")

    def test_取最易等级(self):
        """一词多级时取最低门槛。"""
        assert lookup("run", {"run": "B1"})[1] == "B1"

    def test_查不到给_None(self, wordlist):
        assert lookup("brackety", wordlist) is None

    def test_合成词机械匹配查不到(self, wordlist):
        """goodnight 直查失败——这就是必须过 LLM 的原因。"""
        assert lookup("goodnight", wordlist) is None
        assert lookup("alright", wordlist) is None


class TestMechanicalPass:
    def test_分流已定级与待处理(self, wordlist):
        resolved, pending = mechanical_pass(
            {"wedding": 3, "goodnight": 2, "moved": 1}, wordlist
        )
        assert {e.token for e in resolved} == {"wedding", "moved"}
        assert [e.token for e in pending] == ["goodnight"]

    def test_按词频降序(self, wordlist):
        resolved, _ = mechanical_pass({"wedding": 1, "move": 9}, wordlist)
        assert [e.count for e in resolved] == [9, 1]

    def test_缩略标记为_contraction(self, wordlist):
        resolved, _ = mechanical_pass({"i'm": 5}, wordlist)
        assert resolved[0].category == "contraction"


class TestLLMNormalize:
    def test_归一化后命中词表则用词表等级(self, wordlist):
        """LLM 说 goodnight → good night，回查词表得 A1。"""
        llm = FakeLLM([json.dumps([
            {"token": "goodnight", "lemma": "good night",
             "category": "word", "level": "B1"},   # LLM 估 B1，应被词表 A1 覆盖
        ])])
        out = llm_normalize([VocabEntry("goodnight", 2)], wordlist, llm)
        assert out[0].lemma == "good night"
        assert out[0].level == "A1", "词表等级优先于 LLM 估级"
        assert out[0].source == "llm"

    def test_alright_还原成_all_right(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "alright", "lemma": "all right",
             "category": "word", "level": "A1"},
        ])])
        out = llm_normalize([VocabEntry("alright", 6)], wordlist, llm)
        assert (out[0].lemma, out[0].level) == ("all right", "A1")

    def test_口语变体还原(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "kinda", "lemma": "kind of",
             "category": "contraction", "level": "A2"},
        ])])
        out = llm_normalize([VocabEntry("kinda", 6)], wordlist, llm)
        assert out[0].category == "contraction"
        assert out[0].level == "A2"

    def test_词表未收则用_LLM_估级并留痕(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "hormones", "lemma": "hormone",
             "category": "word", "level": "B2"},
        ])])
        out = llm_normalize([VocabEntry("hormones", 1)], wordlist, llm)
        assert out[0].level == "B2"
        assert "词表未收" in out[0].note

    def test_专有名词不给等级(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "aruba", "lemma": "aruba",
             "category": "proper_noun", "level": None},
        ])])
        out = llm_normalize([VocabEntry("aruba", 2)], wordlist, llm)
        assert out[0].category == "proper_noun"
        assert out[0].level is None
        assert out[0].teachable is False

    def test_碎片标记为_fragment(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "caff", "lemma": "decaf",
             "category": "fragment", "level": None},
        ])])
        out = llm_normalize([VocabEntry("caff", 1)], wordlist, llm)
        assert out[0].category == "fragment"
        assert out[0].teachable is False

    def test_LLM_挂了不阻断(self, wordlist):
        """FakeLLM 回复用尽会抛 LLMError，该批留原样。"""
        out = llm_normalize([VocabEntry("brackety", 2)], wordlist, FakeLLM([]))
        assert out[0].level is None
        assert out[0].source == "rule"

    def test_漏答的_token_保持原样(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "goodnight", "lemma": "good night",
             "category": "word", "level": "A1"},
        ])])
        out = llm_normalize(
            [VocabEntry("goodnight", 1), VocabEntry("brackety", 1)], wordlist, llm
        )
        by = {e.token: e for e in out}
        assert by["goodnight"].level == "A1"
        assert by["brackety"].level is None

    def test_分批调用(self, wordlist):
        llm = FakeLLM(["[]", "[]"])
        pending = [VocabEntry(f"w{i}", 1) for i in range(5)]
        llm_normalize(pending, wordlist, llm, batch=3)
        assert len(llm.calls) == 2, "5 个 token / 每批 3 个 = 2 次调用"

    def test_空输入不调用_LLM(self, wordlist):
        llm = FakeLLM([])
        assert llm_normalize([], wordlist, llm) == []
        assert llm.calls == []


class TestBuildProfile:
    def test_端到端(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "goodnight", "lemma": "good night",
             "category": "word", "level": "A1"},
        ])])
        p = build_profile(
            "0101", ["Goodnight! The wedding moved.", "Goodnight."],
            wordlist, llm,
        )
        assert (p.tokens, p.types) == (5, 4)     # goodnight×2 the wedding moved

        by_level = p.by_level()
        # goodnight 经 LLM 归一化命中 A1；moved→move 机械命中 A1
        assert {e.token for e in by_level["A1"]} == {"goodnight", "moved"}
        assert {e.token for e in by_level["A2"]} == {"wedding"}
        assert [e.token for e in p.unresolved] == ["the"]  # 迷你词表没收 the

    def test_unresolved_只算实词(self, wordlist):
        llm = FakeLLM([json.dumps([
            {"token": "umm", "lemma": "um",
             "category": "interjection", "level": None},
            {"token": "brackety", "lemma": None,
             "category": "word", "level": None},
        ])])
        p = build_profile("x", ["Umm brackety"], wordlist, llm)
        assert [e.token for e in p.unresolved] == ["brackety"], "语气词不算待解决"

    def test_不传_llm_则跳过归一化(self, wordlist):
        p = build_profile("x", ["Goodnight"], wordlist, None)
        assert p.entries[0].level is None
        assert p.entries[0].source == "rule"

    def test_序列化(self, wordlist):
        p = build_profile("0101", ["The wedding"], wordlist, None)
        d = p.to_dict()
        assert d["episode_id"] == "0101"
        assert json.loads(json.dumps(d))["types"] == 2


class TestVocabEntry:
    def test_teachable_需要等级(self):
        assert VocabEntry("x", 1, level="A1").teachable is True
        assert VocabEntry("x", 1).teachable is False

    def test_专有名词即便有等级也不可教(self):
        e = VocabEntry("x", 1, level="A1", category="proper_noun")
        assert e.teachable is False

    def test_lemma_同于_token_时不落盘(self):
        assert "lemma" not in VocabEntry("cat", 1, lemma="cat").to_dict()
