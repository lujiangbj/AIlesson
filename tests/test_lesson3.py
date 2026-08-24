"""三层课程运行时测试。

关键变化：短语和句子从「词的配套」升级为一等教学点 —— 有自己的首触、反向、
跟读、盲听环节。CET-6 用户的课可能一个生词都没有，全是短语和句子。
"""
import pytest

from ailesson.episode import load_episode
from ailesson.lesson3 import SEGMENTS3, LessonRuntime3
from ailesson.packer3 import LessonSpec3
from ailesson.progress import Progress


@pytest.fixture(scope="module")
def e01(mvp_root):
    return load_episode(mvp_root, "peppa-s01e01")


@pytest.fixture
def cet6_spec(e01):
    """CET-6 用户的典型一节：2 个生词 + 2 个短语 + 2 个句子。"""
    return LessonSpec3(
        episode_id=e01.id, index=1, theme="在泥水洼里跳要穿雨靴",
        focus_words=["puddle", "muddy"],
        chunk_ids=["muddy_puddles", "jump_in_puddles"],
        sentence_ids=["s07", "s08"],
    )


@pytest.fixture
def no_word_spec(e01):
    """极端情况：一个生词都没有，全是短语句子。"""
    return LessonSpec3(
        episode_id=e01.id, index=2, theme="只是泥而已",
        focus_words=[],
        chunk_ids=["its_only", "must_check", "look_at_mess"],
        sentence_ids=["s10", "s11", "s15"],
    )


def run_all(rt, correct=True):
    seen = []
    while (c := rt.current()) is not None:
        seen.append(c)
        if c.kind == "assess":
            rt.self_assess(3)
        elif c.needs_answer:
            rt.answer(correct=correct)
        else:
            rt.advance()
    return seen


class TestSegments:
    def test_环节表覆盖三层(self):
        kinds = {s.kind.value for s in SEGMENTS3}
        assert "word_a2i" in kinds
        assert "chunk" in kinds
        assert "sentence" in kinds

    def test_总时长约30分钟(self):
        total = sum(s.minutes for s in SEGMENTS3)
        assert 26 <= total <= 34, total


class TestCET6Lesson:
    @pytest.fixture
    def cards(self, e01, cet6_spec):
        return run_all(LessonRuntime3.build(e01, cet6_spec, Progress()))

    def test_词短语句子都出现(self, cards):
        domains = {c.domain for c in cards if c.needs_answer}
        assert domains >= {"words", "chunks", "sentences"}

    def test_短语有完整形态(self, cards, cet6_spec):
        """短语升级为一等教学点：听辨 + 反向 + 跟读。"""
        for cid in cet6_spec.chunk_ids:
            kinds = {c.kind for c in cards if c.item_id == cid}
            assert "chunk" in kinds, cid
            assert "shadow" in kinds, cid

    def test_句子有完整形态(self, cards, cet6_spec):
        for sid in cet6_spec.sentence_ids:
            kinds = {c.kind for c in cards if c.item_id == sid}
            assert "sentence" in kinds, sid
            assert "shadow" in kinds, sid

    def test_每个教学点至少3次曝光(self, cards, cet6_spec):
        for pid in (cet6_spec.focus_words + cet6_spec.chunk_ids
                    + cet6_spec.sentence_ids):
            hits = [c for c in cards if c.item_id == pid]
            assert len(hits) >= 3, f"{pid} 只出现 {len(hits)} 次"

    def test_句子用原片切片(self, cards):
        s = next(c for c in cards if c.kind == "sentence")
        assert "sentences_audio_clip" in s.prompt_audio

    def test_盲听放本节句子(self, cards, cet6_spec):
        blind = [c for c in cards if c.kind == "assess"]
        assert len(blind) == 1
        assert len(blind[0].audio_clips) == len(cet6_spec.sentence_ids)


class TestNoWordLesson:
    """一个生词都没有的课必须能正常跑完 —— 这是 CET-6 用户的常态。"""

    def test_能跑完(self, e01, no_word_spec):
        rt = LessonRuntime3.build(e01, no_word_spec, Progress())
        cards = run_all(rt)
        assert rt.finished
        assert len(cards) > 10

    def test_没有词卡(self, e01, no_word_spec):
        cards = run_all(LessonRuntime3.build(e01, no_word_spec, Progress()))
        word_quiz = [c for c in cards
                     if c.domain == "words" and c.needs_answer]
        assert word_quiz == []

    def test_短语句子仍有跟读(self, e01, no_word_spec):
        cards = run_all(LessonRuntime3.build(e01, no_word_spec, Progress()))
        shadows = [c for c in cards if c.kind == "shadow"]
        assert len(shadows) >= 3

    def test_统计不为空(self, e01, no_word_spec):
        rt = LessonRuntime3.build(e01, no_word_spec, Progress())
        run_all(rt)
        assert rt.stats["asked"] > 0


class TestMastery:
    def test_课内不产生掌握(self, e01, cet6_spec):
        p = Progress()
        run_all(LessonRuntime3.build(e01, cet6_spec, p), correct=True)
        for cid in cet6_spec.chunk_ids:
            assert not p.is_mastered("chunks", cid), cid
        for sid in cet6_spec.sentence_ids:
            assert not p.is_mastered("sentences", sid), sid

    def test_短语句子也走双向streak(self, e01, cet6_spec):
        """短语和句子同样要双向达标，不能只会听不会认。"""
        p = Progress()
        run_all(LessonRuntime3.build(e01, cet6_spec, p), correct=True)
        e = p.entry("chunks", "muddy_puddles")
        assert e.streak_a2i >= 1
        assert e.streak_i2a >= 1

    def test_答错清零(self, e01, cet6_spec):
        p = Progress()
        run_all(LessonRuntime3.build(e01, cet6_spec, p), correct=False)
        e = p.entry("chunks", "muddy_puddles")
        assert e.streak_a2i == 0


class TestReview:
    def test_复习覆盖三层(self, e01, cet6_spec):
        p = Progress()
        p.record("words", "george", "a2i", True)
        p.record("chunks", "im_peppa", "a2i", True)
        p.record("sentences", "s01", "a2i", True)
        rt = LessonRuntime3.build(e01, cet6_spec, p)
        review = [c for c in rt.cards if c.segment_index == 1]
        assert {c.domain for c in review} == {"words", "chunks", "sentences"}

    def test_本节内容不进复习(self, e01, cet6_spec):
        p = Progress()
        p.record("chunks", "muddy_puddles", "a2i", True)
        rt = LessonRuntime3.build(e01, cet6_spec, p)
        review = [c.item_id for c in rt.cards if c.segment_index == 1]
        assert "muddy_puddles" not in review


class TestResume:
    def test_续上(self, e01, cet6_spec):
        p = Progress()
        rt = LessonRuntime3.build(e01, cet6_spec, p)
        for _ in range(5):
            c = rt.current()
            rt.answer(True) if c.needs_answer else rt.advance()
        rt2 = LessonRuntime3.restore(e01, cet6_spec, p, rt.to_dict())
        assert rt2.cursor == rt.cursor
        assert rt2.current().card_id == rt.current().card_id
        run_all(rt2)
        assert rt2.finished

    def test_有复习和抽检时卡序仍可复现(self, e01, cet6_spec):
        """卡序不能依赖会变的进度。

        pick_review 按 last_at / streak 排序，上课过程中这些都在变。如果 restore
        时重新挑复习词，会得到不同的卡序 → 续上错位到别的卡。
        """
        p = Progress()
        for w in ("george", "little", "brother"):
            p.record("words", w, "a2i", True)
        p.record("chunks", "im_peppa", "a2i", True)
        known = {"words": ["george", "daddy"], "chunks": ["daddy_daddy"]}

        rt = LessonRuntime3.build(e01, cet6_spec, p, known=known)
        before = [c.card_id for c in rt.cards]
        for _ in range(7):
            c = rt.current()
            rt.answer(True) if c.needs_answer else rt.advance()

        rt2 = LessonRuntime3.restore(e01, cet6_spec, p, rt.to_dict(), known=known)
        assert [c.card_id for c in rt2.cards] == before, "整副牌必须一致"
        assert rt2.current().card_id == rt.current().card_id

    def test_快照含复习与抽检选择(self, e01, cet6_spec):
        p = Progress()
        p.record("words", "george", "a2i", True)
        rt = LessonRuntime3.build(e01, cet6_spec, p, known={"words": ["daddy"]})
        snap = rt.to_dict()
        assert "review_picked" in snap
        assert "spot_picked" in snap
        assert snap["spot_picked"]["words"] == ["daddy"]


class TestChoicePool:
    """选项池：词是 4 选 1，短语句子也必须 4 选 1。

    实测发现 chunk/sentence 卡只从本节池里取干扰项：一节 2 个短语就是
    2 选 1，1 个短语只剩唯一选项（点谁都对）。和词一样从全集补足。
    """

    def test_短语选项至少4个且都在全集里(self, e01, cet6_spec):
        cards = run_all(LessonRuntime3.build(e01, cet6_spec, Progress()))
        chunk_ids = {c.id for c in e01.chunks}
        for c in cards:
            if c.domain == "chunks" and c.kind in ("chunk", "i2a"):
                assert len(c.choices) == 4, f"{c.card_id}: {c.choices}"
                assert set(c.choices) <= chunk_ids
                assert c.correct_id in c.choices

    def test_句子选项至少4个且都在全集里(self, e01, cet6_spec):
        cards = run_all(LessonRuntime3.build(e01, cet6_spec, Progress()))
        sent_ids = {s.id for s in e01.sentences}
        for c in cards:
            if c.domain == "sentences" and c.kind in ("sentence", "i2a"):
                assert len(c.choices) == 4, f"{c.card_id}: {c.choices}"
                assert set(c.choices) <= sent_ids
                assert c.correct_id in c.choices

    def test_正确答案在每个选项里只出现一次(self, e01, cet6_spec):
        cards = run_all(LessonRuntime3.build(e01, cet6_spec, Progress()))
        for c in cards:
            if c.needs_answer and c.kind != "shadow":
                assert c.choices.count(c.correct_id) == 1, c.card_id


class TestBonus:
    def test_顺带条目各1题(self, e01):
        spec = LessonSpec3(
            episode_id=e01.id, index=1, theme="t",
            focus_words=["puddle"], chunk_ids=["muddy_puddles"],
            sentence_ids=["s07", "s08"],
            bonus_words=["bath"], bonus_chunks=["lets"], bonus_sentences=["s03"],
        )
        cards = run_all(LessonRuntime3.build(e01, spec, Progress()))
        for pid in ("bath", "lets", "s03"):
            hits = [c for c in cards if c.item_id == pid]
            assert len(hits) == 1, f"{pid}: {len(hits)}"
            assert hits[0].kind != "shadow"
