"""三层打包测试：按教学点（词+短语+句子）计容量。

原来只按词计容量，CET-6 用户勾掉 48 个词后只出 1 节课 —— 但他不会的 26 个短语
和 16 个句子全被忽略了。教学点 = 词 + 短语 + 句子，三者都算容量。
"""
import json

import pytest

from ailesson.assessment import SelfAssessment, build_assessment
from ailesson.episode import load_episode
from ailesson.llm import FakeLLM
from ailesson.packer3 import (
    BATCH_POINTS,
    MAX_POINTS,
    MIN_POINTS,
    _split_assessment,
    pack_course3,
    validate_plan3,
)


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


def reply(lessons):
    return json.dumps({"lessons": lessons}, ensure_ascii=False)


# CET-6 场景：词几乎全会，短语句子不会
CET6_LESSONS = [
    {"theme": "泥水洼里蹦",
     "words": ["puddle", "muddy"], "chunks": ["muddy_puddles", "jump_in_puddles"],
     "sentences": ["s07", "s08"]},
    {"theme": "只是泥而已",
     "words": ["mud", "boot"], "chunks": ["wear_boots", "its_only", "must_check"],
     "sentences": ["s10", "s11"]},
    {"theme": "看你脏成什么样",
     "words": ["goodness"], "chunks": ["look_at_mess", "oh_goodness", "before_sees"],
     "sentences": ["s15", "s16"]},
]


class TestValidate:
    def test_合格通过(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {
            "words": [w for w in all_items["words"]
                      if w not in ("puddle", "muddy", "mud", "boot", "goodness")],
            "chunks": ["im_peppa", "daddy_daddy"],
            "sentences": [],
        })
        assert validate_plan3(e01, CET6_LESSONS, a) == []

    def test_教学点过少不合格(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        bad = [{"theme": "太少", "words": ["puddle"], "chunks": [], "sentences": []}]
        errs = validate_plan3(e01, bad, a)
        assert any("教学点" in e for e in errs)

    def test_教学点过多不合格(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        bad = [{
            "theme": "太多",
            "words": all_items["words"][:8],
            "chunks": all_items["chunks"][:8],
            "sentences": all_items["sentences"][:8],
        }]
        errs = validate_plan3(e01, bad, a)
        assert any("教学点" in e for e in errs)

    def test_引用不在不会池的条目不合格(self, e01, all_items):
        """已勾会的东西不该当重点教。"""
        a = build_assessment(e01.id, all_items, {"chunks": ["muddy_puddles"]})
        bad = [{"theme": "x", "words": ["puddle", "muddy", "mud"],
                "chunks": ["muddy_puddles"], "sentences": ["s07", "s08"]}]
        errs = validate_plan3(e01, bad, a)
        assert any("muddy_puddles" in e for e in errs)

    def test_跨节重复不合格(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        dup = [dict(CET6_LESSONS[0]), dict(CET6_LESSONS[0])]
        errs = validate_plan3(e01, dup, a)
        assert any("重复" in e for e in errs)

    def test_不存在的id不合格(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        bad = [{"theme": "x", "words": ["puddle", "muddy"],
                "chunks": ["no_such_chunk"], "sentences": ["s07", "s99"]}]
        errs = validate_plan3(e01, bad, a)
        assert any("no_such_chunk" in e for e in errs)
        assert any("s99" in e for e in errs)

    def test_一节全是词也合格(self, e01, all_items):
        """零基础用户的课可能全是词，这是允许的。"""
        a = build_assessment(e01.id, all_items, {})
        ok = [{"theme": "认识大家",
               "words": ["peppa", "pig", "george", "little", "brother", "rain"],
               "chunks": [], "sentences": []}]
        assert validate_plan3(e01, ok, a) == []


class TestCET6Flow:
    """核心场景：词几乎全会，课程应围绕短语和句子组织。"""

    @pytest.fixture
    def packed(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {
            "words": [w for w in all_items["words"]
                      if w not in ("puddle", "muddy", "mud", "boot", "goodness")],
            "chunks": ["im_peppa", "daddy_daddy"],
            "sentences": [],
        })
        return a, pack_course3(e01, a, FakeLLM([reply(CET6_LESSONS)]))

    def test_出多节课而不是一节(self, packed):
        """原来只按词算，5 个生词 → 1 节。现在算短语句子 → 多节。"""
        _, plan = packed
        assert len(plan.lessons) >= 3

    def test_每节都有短语或句子(self, packed):
        _, plan = packed
        for l in plan.lessons:
            assert l.chunk_ids or l.sentence_ids, l.theme

    def test_不会的短语句子都被安排(self, packed):
        a, plan = packed
        planned_c = {c for l in plan.lessons for c in l.chunk_ids}
        planned_s = {s for l in plan.lessons for s in l.sentence_ids}
        # 全部不会的短语/句子都要有归属（含 bonus）
        planned_c |= {c for l in plan.lessons for c in l.bonus_chunks}
        planned_s |= {s for l in plan.lessons for s in l.bonus_sentences}
        assert set(a.unknown_chunks) <= planned_c
        assert set(a.unknown_sentences) <= planned_s

    def test_会的短语不当重点(self, packed):
        a, plan = packed
        focus_c = {c for l in plan.lessons for c in l.chunk_ids}
        assert "im_peppa" not in focus_c
        assert "daddy_daddy" not in focus_c

    def test_每节容量在范围内(self, packed):
        _, plan = packed
        for l in plan.lessons:
            assert MIN_POINTS <= l.n_points <= MAX_POINTS, f"{l.theme}: {l.n_points}"


class TestZeroBaseFlow:
    """零基础用户：全都不会，课程仍要成立。"""

    def test_全不会时节数合理(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        # 97 个教学点 / 每节 8 个 ≈ 12 节
        assert a.total_unknown() == 97
        llm = FakeLLM([])       # 走规则兜底，只验节数和覆盖
        plan = pack_course3(e01, a, llm)
        assert plan.fallback is True
        assert 8 <= len(plan.lessons) <= 20

    def test_兜底也不丢条目(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        plan = pack_course3(e01, a, FakeLLM([]))
        got_w = {w for l in plan.lessons for w in l.focus_words + l.bonus_words}
        got_c = {c for l in plan.lessons for c in l.chunk_ids + l.bonus_chunks}
        got_s = {s for l in plan.lessons for s in l.sentence_ids + l.bonus_sentences}
        assert got_w == set(a.unknown_words)
        assert got_c == set(a.unknown_chunks)
        assert got_s == set(a.unknown_sentences)


class TestBatching:
    """大待学池分批打包。

    实测 Friends 157 个教学点时，模型在 <thinking> 里罗列就耗尽 16k 预算，
    产出 44k 字符推演、零 JSON，静默退回机械划分（"第1组/补充N"烂课表）。
    """

    def test_小池不分批(self, e01, all_items):
        """peppa 全不会 97 点，实测单次调用能出结果，不该被卷进分批。"""
        a = build_assessment(e01.id, all_items, {})
        assert a.total_unknown() < BATCH_POINTS
        llm = FakeLLM([reply(CET6_LESSONS)])
        pack_course3(e01, a, llm)
        assert len(llm.calls) == 1

    def test_切分保留全部教学点(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        parts = _split_assessment(a, 20)
        assert len(parts) > 1
        for dom in ("words", "chunks", "sentences"):
            got = [x for p in parts for x in p.unknown[dom]]
            assert got == a.unknown[dom], f"{dom} 有丢失或重复"

    def test_每批都分到语境素材(self, e01, all_items):
        """短语句子按比例分摊——某批全是词会退化成纯词表烂课。"""
        a = build_assessment(e01.id, all_items, {})
        parts = _split_assessment(a, 20)
        with_ctx = sum(1 for p in parts
                       if p.unknown["chunks"] or p.unknown["sentences"])
        assert with_ctx >= len(parts) - 1

    def test_不切空批(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        for p in _split_assessment(a, 15):
            assert p.total_unknown() > 0

    def test_单批时原样返回(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        assert _split_assessment(a, 9999) == [a]

    def test_index_跨批连续(self, e01, all_items):
        """两批各自从 1 编号，合并后必须重排，否则课程表出现重复序号。"""
        a = build_assessment(e01.id, all_items, {})
        big = SelfAssessment(
            episode_id=a.episode_id, known=a.known,
            unknown={"words": a.unknown_words * 5,
                     "chunks": list(a.unknown_chunks),
                     "sentences": list(a.unknown_sentences)},
            how=a.how, at=a.at)
        assert big.total_unknown() > BATCH_POINTS
        plan = pack_course3(e01, big, FakeLLM(["坏"] * 30))
        idx = [l.index for l in plan.lessons]
        assert idx == list(range(1, len(idx) + 1))

    def test_任一批失败则整体标_fallback(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        big = SelfAssessment(
            episode_id=a.episode_id, known=a.known,
            unknown={"words": a.unknown_words * 5, "chunks": [],
                     "sentences": []},
            how=a.how, at=a.at)
        plan = pack_course3(e01, big, FakeLLM(["坏"] * 30))
        assert plan.fallback


class TestRetry:
    def test_不合格触发重试(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {})
        bad = [{"theme": "太少", "words": ["puddle"], "chunks": [], "sentences": []}]
        llm = FakeLLM([reply(bad), reply(CET6_LESSONS)])
        pack_course3(e01, a, llm)
        assert len(llm.calls) == 2
        assert "教学点" in llm.calls[1]["prompt"]

    def test_提示词含三层清单(self, e01, all_items):
        a = build_assessment(e01.id, all_items, {
            "words": ["peppa"], "chunks": ["im_peppa"], "sentences": [],
        })
        llm = FakeLLM([reply(CET6_LESSONS)])
        pack_course3(e01, a, llm)
        p = llm.calls[0]["prompt"]
        assert "muddy_puddles" in p        # 短语清单
        assert "s07" in p                   # 句子清单
        assert "puddle" in p                # 词清单
        # 会的短语不出现在「待学短语」小节里（但可以作为句子的构成信息出现）
        chunk_section = p.split("◆ 待学短语")[1].split("◆")[0]
        assert "im_peppa" not in chunk_section
        # 已会的要单独告知模型别安排
        assert "【已会" in p and "im_peppa" in p.split("【已会")[1]


class TestPersistence:
    def test_往返(self, e01, all_items):
        from ailesson.packer3 import CoursePlan3
        a = build_assessment(e01.id, all_items, {
            "words": [w for w in all_items["words"]
                      if w not in ("puddle", "muddy", "mud", "boot", "goodness")],
            "chunks": ["im_peppa", "daddy_daddy"], "sentences": [],
        })
        plan = pack_course3(e01, a, FakeLLM([reply(CET6_LESSONS)]))
        back = CoursePlan3.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert back == plan
