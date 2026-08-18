"""配图可行性判定测试。用 FakeLLM 打桩，不联网。"""
import json

import pytest

from ailesson.llm import FakeLLM
from ailesson.pickable import GOOD, RISKY, UNPICKABLE, PickVerdict, judge_words


def reply(*rows: dict) -> str:
    return json.dumps(list(rows))


class TestJudgeWords:
    def test_具体名词判_good(self):
        llm = FakeLLM([reply(
            {"word": "spoon", "verdict": "good",
             "subject": "a single silver teaspoon", "reason": None},
        )])
        v = judge_words(["spoon"], llm)[0]
        assert v.verdict == GOOD
        assert v.subject == "a single silver teaspoon"
        assert v.usable

    def test_易混词判_risky_且带区分线索(self):
        """freezer 实测会被画成白箱子，需要冰霜线索。"""
        llm = FakeLLM([reply(
            {"word": "freezer", "verdict": "risky",
             "subject": "an open chest freezer with frost and ice cubes",
             "reason": None},
        )])
        v = judge_words(["freezer"], llm)[0]
        assert v.verdict == RISKY
        assert v.usable
        assert "frost" in v.subject

    def test_连词判_no(self):
        llm = FakeLLM([reply(
            {"word": "although", "verdict": "no",
             "subject": None, "reason": "连词，无实体"},
        )])
        v = judge_words(["although"], llm)[0]
        assert v.verdict == UNPICKABLE
        assert not v.usable
        assert v.subject is None

    def test_集合名词判_no(self):
        """furniture 实测被画成一组家具，孩子只会说 chair。"""
        llm = FakeLLM([reply(
            {"word": "furniture", "verdict": "no",
             "subject": None, "reason": "集合名词"},
        )])
        assert judge_words(["furniture"], llm)[0].verdict == UNPICKABLE

    def test_判_good_但没给_subject_则降级(self):
        """生图没素材，等于不可用。"""
        llm = FakeLLM([reply(
            {"word": "x", "verdict": "good", "subject": "", "reason": None},
        )])
        assert judge_words(["x"], llm)[0].verdict == UNPICKABLE

    def test_非法_verdict_归为_no(self):
        llm = FakeLLM([reply(
            {"word": "x", "verdict": "maybe", "subject": "something"},
        )])
        assert judge_words(["x"], llm)[0].verdict == UNPICKABLE

    def test_LLM_失败时保守判_no(self):
        """宁可漏掉，也别拿不确定的词去浪费生图请求。"""
        out = judge_words(["a", "b"], FakeLLM([]))
        assert all(v.verdict == UNPICKABLE for v in out)
        assert "失败" in out[0].reason

    def test_整批截断则拆半重试(self):
        """输出被 max_tokens 截断时整批解析失败，对半拆开重问。

        实测这是最大的丢词来源：batch=40 时 3/8 批截断，白丢 107 个词。
        """
        llm = FakeLLM([
            "这不是 JSON，模拟被截断",                      # 整批失败
            reply({"word": "a", "verdict": "good", "subject": "A"}),   # 前半
            reply({"word": "b", "verdict": "good", "subject": "B"}),   # 后半
        ])
        out = {v.word: v for v in judge_words(["a", "b"], llm, batch=2)}
        assert out["a"].verdict == GOOD
        assert out["b"].verdict == GOOD
        assert len(llm.calls) == 3, "1 次整批 + 2 次拆半"

    def test_拆半也失败才判_no(self):
        llm = FakeLLM(["坏", "还是坏", "依然坏"])
        out = judge_words(["a", "b"], llm, batch=2)
        assert all(v.verdict == UNPICKABLE for v in out)

    def test_单个词失败不拆半(self):
        """只剩一个词时没法再拆，直接判 no。"""
        llm = FakeLLM(["坏"])
        out = judge_words(["a"], llm, batch=1)
        assert out[0].verdict == UNPICKABLE
        assert len(llm.calls) == 1

    def test_漏答的词判_no(self):
        llm = FakeLLM([reply(
            {"word": "spoon", "verdict": "good", "subject": "a spoon"},
        )])
        out = {v.word: v for v in judge_words(["spoon", "ghost"], llm)}
        assert out["spoon"].verdict == GOOD
        assert out["ghost"].verdict == UNPICKABLE
        assert "未返回" in out["ghost"].reason

    def test_保持输入顺序(self):
        llm = FakeLLM([reply(
            {"word": "b", "verdict": "good", "subject": "B"},
            {"word": "a", "verdict": "good", "subject": "A"},
        )])
        assert [v.word for v in judge_words(["a", "b"], llm)] == ["a", "b"]

    def test_分批(self):
        llm = FakeLLM(["[]", "[]", "[]"])
        judge_words([f"w{i}" for i in range(7)], llm, batch=3)
        assert len(llm.calls) == 3

    def test_空输入不调_LLM(self):
        llm = FakeLLM([])
        assert judge_words([], llm) == []
        assert llm.calls == []

    def test_context_进_prompt(self):
        llm = FakeLLM(["[]"])
        judge_words(["x"], llm, context="《老友记》S1E1")
        assert "老友记" in llm.calls[0]["prompt"]


class TestSenseBinding:
    """例句进 prompt，绑定剧中的那个义项。

    不传例句时 LLM 会挑最好画的同形异义词——实测 split 被画成香蕉船
    （剧里是"平分"）、pot 被画成汤锅（剧里是咖啡壶）。
    """

    def test_例句进_prompt(self):
        llm = FakeLLM(["[]"])
        judge_words(["split"], llm, senses={"split": ["Split it?"]})
        p = llm.calls[0]["prompt"]
        assert "Split it?" in p
        assert "义项" in p

    def test_不传例句时用裸词表(self):
        llm = FakeLLM(["[]"])
        judge_words(["split"], llm)
        assert llm.calls[0]["prompt"] == '["split"]'

    def test_拆半重试也带例句(self):
        llm = FakeLLM(["坏", "[]", "[]"])
        judge_words(["a", "b"], llm, batch=2, senses={"a": ["A line"]})
        assert "A line" in llm.calls[-2]["prompt"]


class TestTextLeakGuard:
    """代码兜底：prompt 明令禁止印字，实测仍有 20/242 个 subject 泄漏。

    图上印了词或数字，听音选图就等于把答案写在卡面上，题目作废。
    """

    @pytest.mark.parametrize("subject", [
        "a runner with a large '4th / LAST' ribbon on their chest",
        "five runners, the fifth labeled with number 5",
        "a scoreboard showing 1,000 points",
        "a podium with numerals 1st through 5th",
        "a chalkboard with the text 2 + 3 = 5 written on it",
        "a gold nameplate on a desk",
        "a bib reading the word LAST",
    ])
    def test_要求印字的_subject_被拦(self, subject):
        llm = FakeLLM([reply(
            {"word": "x", "verdict": "good", "subject": subject},
        )])
        v = judge_words(["x"], llm)[0]
        assert v.verdict == UNPICKABLE, f"未拦住: {subject}"
        assert v.subject is None
        assert v.reason == "须依赖文字"

    @pytest.mark.parametrize("subject", [
        "a single silver teaspoon on a plain background",
        "five identical cups in a row, a hand lifting the fifth one",
        "two piles of apples being merged into one by a large hand gesture",
        "a credit card with a chip and magnetic stripe",
    ])
    def test_正常_subject_不受影响(self, subject):
        llm = FakeLLM([reply(
            {"word": "x", "verdict": "good", "subject": subject},
        )])
        assert judge_words(["x"], llm)[0].verdict == GOOD


class TestPickVerdict:
    def test_usable_含_good_和_risky(self):
        assert PickVerdict("x", GOOD).usable
        assert PickVerdict("x", RISKY).usable
        assert not PickVerdict("x", UNPICKABLE).usable

    def test_序列化省略空字段(self):
        d = PickVerdict("x", GOOD).to_dict()
        assert d == {"word": "x", "verdict": "good"}
