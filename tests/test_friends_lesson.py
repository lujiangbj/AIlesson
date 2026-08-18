"""Friends 资产 → lesson JSON 转换测试。

课程引擎读的是 MVP 那套 schema，Friends 这条线产出的是另一套结构，
转换层必须保证：资产不齐的词不进池、覆盖关系算得出、干扰项不重复。
"""
import json

import pytest

from ailesson.episode import Episode, Word, Chunk, Sentence
from ailesson.friends_lesson import build_lesson, coverage_report


@pytest.fixture
def ep_dir(tmp_path):
    """造一个最小的 Friends 资产目录。"""
    d = tmp_path / "0101"
    d.mkdir()

    (d / "cards.json").write_text(json.dumps({"cards": {
        "spoon": {"file": "cards/spoon.png", "verdict": "good"},
        "stairs": {"file": "cards/stairs.png", "verdict": "good"},
        "noaudio": {"file": "cards/noaudio.png", "verdict": "good"},
    }}))

    (d / "audio.json").write_text(json.dumps({
        "words": {
            "spoon": {"file": "audio/words/spoon.mp3"},
            "stairs": {"file": "audio/words/stairs.mp3"},
            "noimage": {"file": "audio/words/noimage.mp3"},
        },
        "chunks": {
            "grab_a": {"audio_tts": "audio/chunks/grab_a.mp3",
                       "audio_tts_slow": "audio/chunks/grab_a_slow.mp3"},
            "no_audio_chunk": {},
        },
        "lesson_sentences": {
            "s01": {"audio_tts": "audio/lesson_sentences/s01.mp3",
                    "audio_tts_slow": "audio/lesson_sentences/s01_slow.mp3"},
            "s02": {"audio_tts": "audio/lesson_sentences/s02.mp3"},
        },
    }))

    (d / "lesson.json").write_text(json.dumps({
        "title": "The Pilot",
        "chunks": [
            {"id": "grab_a", "text": "Grab a", "meaning_zh": "拿一个",
             "covers_words": ["grab", "a", "spoon"]},
            {"id": "no_audio_chunk", "text": "no audio", "meaning_zh": "x",
             "covers_words": ["spoon"]},
        ],
        "sentences": [
            {"id": "s01", "text": "Grab a spoon and eat.", "meaning_zh": "拿勺子",
             "speaker": "Joey", "chunks": ["grab_a"], "key_words": ["spoon"]},
            {"id": "s02", "text": "Up the stairs now.", "meaning_zh": "上楼",
             "speaker": "Ross", "chunks": [], "key_words": ["stairs"]},
            {"id": "s03", "text": "No audio here.", "meaning_zh": "无音",
             "speaker": "X", "chunks": [], "key_words": ["spoon"]},
        ],
    }))

    (d / "lesson_images.json").write_text(json.dumps({"images": {
        "grab_a": {"file": "lesson_cards/grab_a.png", "kind": "chunk"},
        "s01": {"file": "lesson_cards/s01.png", "kind": "sentence"},
    }}))

    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps({"entries": [
        {"token": "spoon", "count": 9, "level": "A2", "category": "word"},
        {"token": "stairs", "count": 4, "level": "A2", "category": "word"},
        {"token": "noaudio", "count": 2, "level": "B1", "category": "word"},
        {"token": "noimage", "count": 1, "level": "B1", "category": "word"},
    ]}))
    return d, vocab


@pytest.fixture
def lesson(ep_dir):
    d, v = ep_dir
    return build_lesson(d, vocab_path=v, title="The Pilot")


class TestWords:
    def test_只收图音都齐的词(self, lesson):
        """缺图或缺音的词过不了环节 3/4，宁可少而完整。"""
        assert {w["lemma"] for w in lesson["words"]} == {"spoon", "stairs"}

    def test_按词频降序(self, lesson):
        assert [w["freq"] for w in lesson["words"]] == [9, 4]

    def test_路径带_friends_前缀(self, lesson):
        w = lesson["words"][0]
        assert w["image"] == "friends/0101/cards/spoon.png"
        assert w["audio"] == "friends/0101/audio/words/spoon.mp3"

    def test_没慢速版时复用常速(self, lesson):
        w = lesson["words"][0]
        assert w["audio_slow"] == w["audio"]

    def test_带_cefr_等级(self, lesson):
        assert lesson["words"][0]["cefr"] == "A2"


class TestChunks:
    def test_缺音频的_chunk_剔除(self, lesson):
        assert [c["id"] for c in lesson["chunks"]] == ["grab_a"]

    def test_covers_words_只留词表内的(self, lesson):
        """"a" 和 "grab" 不在词表里，会让覆盖关系算错。"""
        assert lesson["chunks"][0]["covers_words"] == ["spoon"]

    def test_慢速版正常带上(self, lesson):
        assert lesson["chunks"][0]["audio_tts_slow"].endswith("grab_a_slow.mp3")

    def test_有图则带图(self, lesson):
        assert lesson["chunks"][0]["image"] == "friends/0101/lesson_cards/grab_a.png"


class TestSentences:
    def test_缺音频的句子剔除(self, lesson):
        assert [s["id"] for s in lesson["sentences"]] == ["s01", "s02"]

    def test_文本字段名对齐_MVP(self, lesson):
        """episode.py 读的是 text_admin_only。"""
        assert lesson["sentences"][0]["text_admin_only"] == "Grab a spoon and eat."

    def test_无原片音轨时用_TTS_顶(self, lesson):
        s = lesson["sentences"][0]
        assert s["audio_clip"] == s["audio_tts"]

    def test_只引用存在的_chunk(self, lesson):
        """no_audio_chunk 被剔除了，句子不能再引用它。"""
        for s in lesson["sentences"]:
            for cid in s["chunks"]:
                assert cid in {c["id"] for c in lesson["chunks"]}

    def test_没图的句子_image_为空(self, lesson):
        s2 = [s for s in lesson["sentences"] if s["id"] == "s02"][0]
        assert s2["image"] == ""

    def test_既无词又无_chunk_的句子剔除(self, ep_dir):
        d, v = ep_dir
        raw = json.loads((d / "lesson.json").read_text())
        raw["sentences"].append({"id": "s04", "text": "Nothing.",
                                 "chunks": [], "key_words": ["notinvocab"]})
        (d / "lesson.json").write_text(json.dumps(raw))
        au = json.loads((d / "audio.json").read_text())
        au["lesson_sentences"]["s04"] = {"audio_tts": "a.mp3"}
        (d / "audio.json").write_text(json.dumps(au))

        out = build_lesson(d, vocab_path=v)
        assert "s04" not in [s["id"] for s in out["sentences"]]


class TestDistractors:
    def test_每词三个干扰项或用尽(self, lesson):
        for lemma, ds in lesson["distractors"].items():
            assert len(ds) <= 3
            assert lemma not in ds, "自己不能当干扰项"

    def test_干扰项不重复(self, lesson):
        for ds in lesson["distractors"].values():
            assert len(ds) == len(set(ds))

    def test_干扰项都在词表内(self, lesson):
        lemmas = {w["lemma"] for w in lesson["words"]}
        for ds in lesson["distractors"].values():
            assert set(ds) <= lemmas

    def test_可复现(self, ep_dir):
        d, v = ep_dir
        a = build_lesson(d, vocab_path=v)["distractors"]
        b = build_lesson(d, vocab_path=v)["distractors"]
        assert a == b, "同 seed 应产出相同干扰项"


class TestEngineCompat:
    def test_能被_Episode_直接加载(self, lesson):
        """最关键的一条：转换结果必须喂得进课程引擎。"""
        ep = Episode(
            id=lesson["id"], title=lesson["title"], level=lesson["level"],
            duration_seconds=lesson["duration_seconds"],
            words=[Word.from_raw(d) for d in lesson["words"]],
            chunks=[Chunk.from_raw(d) for d in lesson["chunks"]],
            sentences=[Sentence.from_raw(d) for d in lesson["sentences"]],
            distractors=lesson["distractors"],
        )
        assert ep.word("spoon").freq == 9
        assert ep.chunk("grab_a").text == "Grab a"
        assert ep.sentence("s01").text == "Grab a spoon and eat."

    def test_覆盖关系算得出(self, lesson):
        ep = Episode(
            id=lesson["id"], title="", level=2, duration_seconds=0,
            words=[Word.from_raw(d) for d in lesson["words"]],
            chunks=[Chunk.from_raw(d) for d in lesson["chunks"]],
            sentences=[Sentence.from_raw(d) for d in lesson["sentences"]],
        )
        assert ep.words_covered_by_sentence("s01") == {"spoon"}
        assert ep.words_covered_by_sentence("s02") == {"stairs"}


class TestCoverageReport:
    def test_统计孤儿词(self, lesson):
        r = coverage_report(lesson)
        assert r["words"] == 2
        assert r["words_in_sentences"] == 2
        assert r["orphan_words"] == []

    def test_孤儿词被识别(self, ep_dir):
        """没被任何句子覆盖的词 = FR-3.4 的"顺带词"。"""
        d, v = ep_dir
        raw = json.loads((d / "lesson.json").read_text())
        raw["sentences"] = [s for s in raw["sentences"] if s["id"] != "s02"]
        (d / "lesson.json").write_text(json.dumps(raw))
        r = coverage_report(build_lesson(d, vocab_path=v))
        assert r["orphan_words"] == ["stairs"]

    def test_统计带图数量(self, lesson):
        r = coverage_report(lesson)
        assert r["chunks_with_image"] == 1
        assert r["sentences_with_image"] == 1
