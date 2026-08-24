"""端到端：三层勾选 → 打包 → 上课 → 报告。"""
import json

import pytest

from ailesson.course.cache import LLMCache
from ailesson.session import CourseSession
from ailesson.contract.episode import load_episode
from ailesson.infra.llm import FakeLLM


@pytest.fixture(scope="module")
def e01(mvp_root):
    return load_episode(mvp_root, "peppa-s01e01")


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


def reply():
    return json.dumps({"lessons": CET6_LESSONS}, ensure_ascii=False)


def cet6_known(e01):
    """CET-6 用户：词几乎全会，短语句子基本不会。"""
    return {
        "words": [w.lemma for w in e01.words
                  if w.lemma not in ("puddle", "muddy", "mud", "boot", "goodness")],
        "chunks": ["im_peppa", "daddy_daddy"],
        "sentences": [],
    }


class TestCET6Flow:
    @pytest.fixture
    def session(self, e01):
        s = CourseSession(e01, FakeLLM([reply()]))
        a = s.submit_checklist(cet6_known(e01))
        from ailesson.course.planner import pack_course
        s.plan = pack_course(e01, a, s.llm)
        return s

    def test_教学点总数远超词数(self, session):
        a = session.assessment
        assert len(a.unknown_words) == 5
        assert a.total_unknown() > 40, "短语句子才是主体"

    def test_出多节课(self, session):
        assert len(session.plan.lessons) >= 3

    def test_每节都能上完(self, session):
        for l in session.plan.lessons:
            rt = session.start_lesson(l.index)
            assert rt is not None
            n = 0
            while (c := rt.current()) is not None and n < 300:
                n += 1
                if c.kind == "assess":
                    rt.self_assess(3)
                elif c.needs_answer:
                    rt.answer(correct=True)
                else:
                    rt.advance()
            assert rt.finished
            session.finish_lesson(rt)
        assert len(session.completed_lessons) == len(session.plan.lessons)

    def test_报告用人话不用内部id(self, session):
        """实跑发现：报告写 "s16 guess_what weve_been_doing"，用户看不懂。"""
        rt = session.start_lesson(1)
        while (c := rt.current()) is not None:
            rt.self_assess(2) if c.kind == "assess" else (
                rt.answer(False) if c.needs_answer else rt.advance())
        r = session.finish_lesson(rt)
        assert r.review_next, "全答错该有复习项"
        for label in r.review_next:
            assert not label.startswith("s0"), f"露出了句子 id: {label}"
            assert "_" not in label, f"露出了短语 id: {label}"

    def test_label_of三层(self, session):
        assert session.label_of("words", "puddle") == "puddle"
        assert session.label_of("chunks", "muddy_puddles") == "muddy puddles"
        assert session.label_of("sentences", "s07").startswith("Peppa loves")

    def test_报告口径是教学点(self, session):
        rt = session.start_lesson(1)
        while (c := rt.current()) is not None:
            rt.self_assess(2) if c.kind == "assess" else (
                rt.answer(True) if c.needs_answer else rt.advance())
        r = session.finish_lesson(rt)
        spec = session.spec_for(1)
        assert r.points_learned == spec.n_points
        assert r.n_words == len(spec.focus_words)
        assert r.n_chunks == len(spec.chunk_ids)
        assert r.n_sentences == len(spec.sentence_ids)
        assert r.theme == "泥水洼里蹦"
        assert r.next_theme == "只是泥而已"

    def test_抽检答错打回(self, session):
        rt = session.start_lesson(1)
        target = None
        while (c := rt.current()) is not None:
            if c.segment_index == 2:
                target = (c.domain, c.item_id)
                rt.answer(correct=False)
                break
            rt.answer(True) if c.needs_answer else rt.advance()
        if target is None:
            pytest.skip("本节没有抽检卡")
        while (c := rt.current()) is not None:
            rt.self_assess(3) if c.kind == "assess" else (
                rt.answer(True) if c.needs_answer else rt.advance())
        session.finish_lesson(rt)
        dom, item = target
        assert item in session.assessment.unknown[dom]

    def test_跨节复习让短语句子掌握(self, session):
        for l in session.plan.lessons:
            rt = session.start_lesson(l.index)
            while (c := rt.current()) is not None:
                rt.self_assess(3) if c.kind == "assess" else (
                    rt.answer(True) if c.needs_answer else rt.advance())
            session.finish_lesson(rt)
        p = session.progress
        mastered = [c for c in ("muddy_puddles", "jump_in_puddles", "wear_boots")
                    if p.is_mastered("chunks", c)]
        assert mastered, "上完 3 节，早期短语该有掌握的"


class TestPersistence:
    def test_往返(self, e01):
        from ailesson.course.planner import pack_course
        s = CourseSession(e01, FakeLLM([reply()]))
        a = s.submit_checklist(cet6_known(e01))
        s.plan = pack_course(e01, a, s.llm)
        rt = s.start_lesson(1)
        for _ in range(6):
            c = rt.current()
            rt.answer(True) if c.needs_answer else rt.advance()

        snap = json.loads(json.dumps(s.to_dict(lesson_runtime=rt)))
        s2, rt2 = CourseSession.restore(e01, FakeLLM([]), snap)
        assert s2.assessment == s.assessment
        assert len(s2.plan.lessons) == len(s.plan.lessons)
        assert rt2.cursor == rt.cursor
        assert rt2.current().card_id == rt.current().card_id


class TestCache:
    def test_打包结果缓存(self, e01, tmp_path):
        c = LLMCache(tmp_path)
        s = CourseSession(e01, FakeLLM([reply()]))
        a = s.submit_checklist(cet6_known(e01))
        c.get_or_build_plan(e01, a, FakeLLM([reply()]))
        llm = FakeLLM([])           # 一调用就报错
        plan = c.get_or_build_plan(e01, a, llm)
        assert plan.lessons
        assert llm.calls == []

    def test_勾选变了要重算(self, e01, tmp_path):
        c = LLMCache(tmp_path)
        s = CourseSession(e01, FakeLLM([reply()]))
        a1 = s.submit_checklist(cet6_known(e01))
        c.get_or_build_plan(e01, a1, FakeLLM([reply()]))
        a2 = s.submit_checklist({"words": [], "chunks": [], "sentences": []})
        llm = FakeLLM([reply()])
        c.get_or_build_plan(e01, a2, llm)
        assert llm.calls, "不同勾选必须重算"

    def test_兜底不缓存(self, e01, tmp_path):
        c = LLMCache(tmp_path)
        s = CourseSession(e01, FakeLLM([]))
        a = s.submit_checklist(cet6_known(e01))
        plan = c.get_or_build_plan(e01, a, FakeLLM([]))
        assert plan.fallback is True
        llm = FakeLLM([reply()])
        c.get_or_build_plan(e01, a, llm)
        assert llm.calls, "兜底结果不该被缓存"
