"""中途退出与续上测试。

调试期和真实使用都需要：一进课就出不来，只能 reset 整集重来。
runtime 快照本来就随 save() 落盘，缺的只是一个"离开但不丢进度"的出口。
"""
import pytest

from ailesson.course.assessment import build_assessment
from ailesson.session import CourseSession
from ailesson.contract.episode import load_episode
from ailesson.infra.llm import FakeLLM
from ailesson.contract.lesson_spec import CoursePlan, LessonSpec


@pytest.fixture(scope="module")
def e01(mvp_root):
    return load_episode(mvp_root, "peppa-s01e01")


@pytest.fixture
def session(e01):
    """造一个已打包、可开课的会话。"""
    s = CourseSession(episode=e01, llm=FakeLLM([]))
    items = {
        "words": [w.lemma for w in e01.words],
        "chunks": [c.id for c in e01.chunks],
        "sentences": [s2.id for s2 in e01.sentences],
    }
    s.assessment = build_assessment(e01.id, items, {})
    s.plan = CoursePlan(episode_id=e01.id, lessons=[LessonSpec(
        episode_id=e01.id, index=1, theme="测试节",
        focus_words=[w.lemma for w in e01.words[:6]],
        chunk_ids=[c.id for c in e01.chunks[:2]],
        sentence_ids=[x.id for x in e01.sentences[:2]],
    )])
    return s


class TestSnapshotRoundTrip:
    def test_中途快照能续上同一张卡(self, e01, session):
        rt = session.start_lesson(1)
        for _ in range(3):
            rt.answer(correct=True)
        cursor, total = rt.cursor, len(rt.cards)

        snap = session.to_dict(lesson_runtime=rt)
        s2, rt2 = CourseSession.restore(e01, FakeLLM([]), snap)
        assert rt2 is not None, "快照里有 lesson，应该恢复出 runtime"
        assert rt2.cursor == cursor
        assert len(rt2.cards) == total

    def test_不带_runtime_的快照恢复后无进行中课(self, e01, session):
        session.start_lesson(1)
        s2, rt2 = CourseSession.restore(
            e01, FakeLLM([]), session.to_dict())     # 刻意不传 runtime
        assert rt2 is None
        assert s2.plan is not None, "课程表仍在，只是没有进行中的课"

    def test_退出不影响已完成列表(self, e01, session):
        session.completed_lessons = [1]
        snap = session.to_dict()
        s2, _ = CourseSession.restore(e01, FakeLLM([]), snap)
        assert s2.completed_lessons == [1]

    def test_答题统计跟着快照走(self, e01, session):
        rt = session.start_lesson(1)
        rt.answer(correct=True)
        rt.answer(correct=False)
        snap = session.to_dict(lesson_runtime=rt)
        _, rt2 = CourseSession.restore(e01, FakeLLM([]), snap)
        assert rt2.stats == rt.stats
        assert rt2.wrong_items == rt.wrong_items


class TestTutorReturnsLine:
    """讲解必须**返回**文本，不能只塞队列。

    muted=True 时 VoiceQueue.push 直接丢弃，只塞队列的话调用方拿不到
    讲解，会退回自己的兜底句——实测表现为答错时永远显示
    "single，单身的。"，LLM 写的讲解全丢了。
    """

    def test_答错讲解有返回值(self):
        from ailesson.classroom.voice import TutorVoice

        class _T:
            def speak(self, text): return b""

        v = TutorVoice(llm=FakeLLM(["single 是单身，你选的 pulled 是拽出"]),
                       tts=_T(), muted=True)
        line = v.on_wrong("single", "pulled", meaning_zh="单身的",
                          chosen_zh="拽出")
        assert line and "单身" in line

    def test_muted_下仍返回文本(self):
        from ailesson.classroom.voice import TutorVoice

        class _T:
            def speak(self, text): return b""

        v = TutorVoice(llm=FakeLLM(["讲解内容"]), tts=_T(), muted=True)
        assert v.on_wrong("a", "b", meaning_zh="甲", chosen_zh="乙") == "讲解内容"
        assert v.queue.pending == [], "muted 时不该进播放队列"

    def test_LLM_失败时兜底也带词义(self):
        from ailesson.classroom.voice import TutorVoice

        class _T:
            def speak(self, text): return b""

        v = TutorVoice(llm=FakeLLM([]), tts=_T(), muted=True)
        line = v.on_wrong("single", "pulled", meaning_zh="单身的",
                          chosen_zh="拽出")
        assert "单身的" in line and "拽出" in line

    def test_答对讲解有返回值(self):
        from ailesson.classroom.voice import TutorVoice

        class _T:
            def speak(self, text): return b""

        v = TutorVoice(llm=FakeLLM(["single 就是单身，常说 I'm single"]),
                       tts=_T(), muted=True)
        line = v.on_confirm("single", meaning_zh="单身的")
        assert "single" in line


class TestSelectionProbePersist:
    """selection 和 probe 早先没落盘，重启后全变 None——
    排查"动态挑选有没有生效"时看不到任何痕迹。"""

    def test_selection_落盘并恢复(self, e01, session):
        session.selection = {"source": "probe",
                             "chunks": [{"id": "c1", "score": 4.0}],
                             "sentences": []}
        s2, _ = CourseSession.restore(
            e01, FakeLLM([]), session.to_dict())
        assert s2.selection["source"] == "probe"
        assert len(s2.selection["chunks"]) == 1

    def test_probe_落盘并恢复(self, e01, session):
        session.probe = {"asked": ["a", "b"], "answers": {"a": True},
                         "calibration": {"threshold": 2.5, "confident": True}}
        s2, _ = CourseSession.restore(
            e01, FakeLLM([]), session.to_dict())
        assert s2.probe["calibration"]["threshold"] == 2.5
        assert s2.probe["answers"] == {"a": True}

    def test_缺字段时不炸(self, e01, session):
        """兼容老快照——那时还没这两个字段。"""
        snap = session.to_dict()
        snap.pop("selection", None)
        snap.pop("probe", None)
        s2, _ = CourseSession.restore(e01, FakeLLM([]), snap)
        assert s2.selection == {} and s2.probe == {}
