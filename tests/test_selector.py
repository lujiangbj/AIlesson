"""动态挑 chunk / 句子的测试。

设计要点（用户纠正）：选句看**教学价值**（生词密度、地道结构、连读点），
不看覆盖率；且挑选发生在勾选单词**之后**，不同词池挑出不同素材。
"""
import pytest

from ailesson.contract.episode import Chunk, Episode, Sentence, Word
from ailesson.course.selector import (
    build_pool,
    pick_chunks,
    pick_sentences,
    score_sentence,
)


def W(lemma, freq=1):
    return Word(lemma=lemma, freq=freq, audio="a", audio_slow="a",
                image="i", meaning_zh="")


def C(cid, text, covers=()):
    return Chunk(id=cid, text=text, meaning_zh="", audio_tts="a",
                 audio_tts_slow="a", image="i", covers_words=tuple(covers))


def S(sid, text, chunks=(), keys=()):
    return Sentence(id=sid, text=text, meaning_zh="", audio_tts="a",
                    audio_tts_slow="a", audio_clip="a", image="i",
                    chunk_ids=tuple(chunks), key_words=tuple(keys))


@pytest.fixture
def ep():
    return Episode(
        id="t", title="", level=2, duration_seconds=0,
        words=[W("spoon"), W("gravy"), W("hump"), W("wedding")],
        chunks=[
            C("grab_a", "grab a spoon", ["spoon"]),
            C("kind_of", "kind of", []),
            C("plain", "on the table", []),
        ],
        sentences=[
            S("s01", "Grab a spoon and eat the gravy now.",
              chunks=["grab_a"], keys=["spoon", "gravy"]),
            S("s02", "It is on the table.", chunks=["plain"]),
            S("s03", "I'm gonna grab a spoon, y'know?", chunks=["grab_a"],
              keys=["spoon"]),
        ],
    )


class TestScoreSentence:
    def test_含待学词得分高(self):
        a = score_sentence("The gravy is on the spoon here", {"gravy", "spoon"})
        b = score_sentence("The gravy is on the spoon here", set())
        assert a.score > b.score

    def test_无待学点扣分(self):
        s = score_sentence("It is on the table now", set())
        assert s.score < 0
        assert "无待学点" in s.reasons

    def test_生词过密扣分(self):
        """全是生词学不动——不能只追生词数量。"""
        dense = score_sentence("gravy hump wedding spoon chalk purse",
                               {"gravy", "hump", "wedding", "spoon",
                                "chalk", "purse"})
        good = score_sentence("Please grab the gravy and the spoon",
                              {"gravy", "spoon"})
        assert good.score > dense.score
        assert "生词过密" in dense.reasons

    def test_地道口语结构加分(self):
        a = score_sentence("I'm gonna grab a spoon", {"spoon"})
        b = score_sentence("I will take a spoon", {"spoon"})
        assert a.score > b.score

    def test_缩略形式加分(self):
        a = score_sentence("I'm sure it's the gravy", {"gravy"})
        b = score_sentence("I am sure it is the gravy", {"gravy"})
        assert a.score > b.score
        assert any("缩略" in r for r in a.reasons)

    def test_长度适中加分(self):
        mid = score_sentence("Please grab the gravy from the kitchen table",
                             {"gravy"})
        long = score_sentence("Please grab the gravy " + "and more words " * 5,
                              {"gravy"})
        assert mid.score > long.score

    def test_过短扣分(self):
        s = score_sentence("Grab gravy", {"gravy"})
        assert "过短" in s.reasons

    def test_待学_chunk_加分(self):
        a = score_sentence("It is kind of nice", set(),
                           chunk_ids=("kind_of",), unknown_chunks={"kind_of"})
        b = score_sentence("It is kind of nice", set(),
                           chunk_ids=("kind_of",), unknown_chunks=set())
        assert a.score > b.score

    def test_记录待学词(self):
        s = score_sentence("The gravy and the spoon", {"gravy", "spoon"})
        assert s.new_words == ["gravy", "spoon"]

    def test_空文本(self):
        assert score_sentence("", {"x"}).score == 0.0


class TestPickSentences:
    def test_按分数降序(self, ep):
        out = pick_sentences(ep, {"spoon", "gravy"}, set(), min_score=-99)
        assert [s.score for s in out] == sorted(
            (s.score for s in out), reverse=True)

    def test_过滤低分句(self, ep):
        """s02 无待学点，不该入选。"""
        out = pick_sentences(ep, {"spoon", "gravy"}, set())
        assert "s02" not in [s.id for s in out]

    def test_limit_控制规模(self, ep):
        """早先 76 句全进池，打包出 43 节课——必须限量。"""
        out = pick_sentences(ep, {"spoon", "gravy"}, set(), limit=1,
                             min_score=-99)
        assert len(out) == 1

    def test_不同词池挑出不同句子(self, ep):
        """核心：同一集，词汇量不同的人该练不同素材。"""
        a = {s.id for s in pick_sentences(ep, {"gravy"}, set(), min_score=-99)}
        b = {s.id for s in pick_sentences(ep, {"wedding"}, set(), min_score=-99)}
        top_a = pick_sentences(ep, {"gravy"}, set(), min_score=-99)[0].id
        top_b = pick_sentences(ep, {"spoon"}, set(), min_score=-99)[0].id
        assert a and b
        assert top_a == "s01", "含 gravy 的句子该排第一"
        assert top_b in ("s01", "s03")

    def test_同分按剧情顺序(self, ep):
        out = pick_sentences(ep, set(), set(), min_score=-99)
        same = [s.id for s in out if s.score == out[0].score]
        assert same == sorted(same)


class TestPickChunks:
    def test_入选句里的_chunk_优先(self, ep):
        out = pick_chunks(ep, set(), {"s01"})
        assert out[0].id == "grab_a"
        assert any("入选句子" in r for r in out[0].reasons)

    def test_含待学词的_chunk_入选(self, ep):
        out = pick_chunks(ep, {"spoon"}, set())
        assert "grab_a" in [c.id for c in out]

    def test_零分_chunk_剔除(self, ep):
        """plain 既不在入选句里也无待学词，且非地道搭配。"""
        out = pick_chunks(ep, set(), set())
        assert "plain" not in [c.id for c in out]

    def test_地道搭配加分(self, ep):
        out = pick_chunks(ep, set(), set())
        assert "kind_of" in [c.id for c in out]

    def test_复用度加分(self, ep):
        """grab_a 被 s01/s03 引用两次。"""
        out = {c.id: c for c in pick_chunks(ep, set(), set())}
        assert any("复用" in r for r in out["grab_a"].reasons)

    def test_limit(self, ep):
        assert len(pick_chunks(ep, {"spoon"}, {"s01"}, limit=1)) == 1


class TestRestrict:
    """听力探测（probe.py）给出实测的"听不懂"集合时，只在其中挑。

    探测是测量，比这里的启发式打分可信；打分只负责按教学价值限量排序。
    """

    def test_只在实测范围内挑句子(self, ep):
        pool = build_pool(ep, {"spoon", "gravy"},
                          restrict_sentences={"s03"})
        assert {s.id for s in pool["sentences"]} <= {"s03"}

    def test_只在实测范围内挑_chunk(self, ep):
        pool = build_pool(ep, {"spoon"}, restrict_chunks={"kind_of"})
        assert {c.id for c in pool["chunks"]} <= {"kind_of"}

    def test_空集合给出空结果(self, ep):
        """探测显示全都听懂了 → 没东西要练，不该兜底塞满。"""
        pool = build_pool(ep, {"spoon"}, restrict_sentences=set(),
                          restrict_chunks=set())
        assert pool["sentences"] == [] and pool["chunks"] == []

    def test_不传_restrict_则全集参与(self, ep):
        pool = build_pool(ep, {"spoon", "gravy"})
        assert len(pool["sentences"]) >= 1

    def test_仍按教学价值限量(self, ep):
        """探测可能给出 71 句，必须限量否则打包出几十节课。"""
        pool = build_pool(ep, {"spoon", "gravy"},
                          restrict_sentences={"s01", "s02", "s03"},
                          sentence_limit=1)
        assert len(pool["sentences"]) <= 1


class TestBuildPool:
    def test_同时给出_chunk_和句子(self, ep):
        pool = build_pool(ep, {"spoon", "gravy"})
        assert pool["sentences"] and pool["chunks"]

    def test_不追求覆盖所有待学词(self, ep):
        """wedding 没出现在任何句子里，但这不是问题——词在别处能练到。"""
        pool = build_pool(ep, {"spoon", "gravy", "wedding"})
        covered = {w for s in pool["sentences"] for w in s.new_words}
        assert "wedding" not in covered
        assert pool["sentences"], "不该因为覆盖不全就返回空"

    def test_规模受限(self, ep):
        pool = build_pool(ep, {"spoon", "gravy"}, sentence_limit=1,
                          chunk_limit=1)
        assert len(pool["sentences"]) <= 1
        assert len(pool["chunks"]) <= 1

    def test_空词池仍能挑出地道素材(self, ep):
        """全会的人也该有东西练——地道结构和连读点。"""
        pool = build_pool(ep, set())
        assert pool["chunks"], "kind_of 是地道搭配，该留下"
