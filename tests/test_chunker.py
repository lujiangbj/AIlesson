"""短语/句子抽取测试。用 FakeLLM 打桩，不联网。

核心约束：句子文本必须一字不改沿用原话（环节 9 要放原片音轨），
chunk 必须是原句里连续出现的片段（不许 LLM 拼凑）。
"""
import json

import pytest

from ailesson.chunker import candidate_lines, extract
from ailesson.llm import FakeLLM


def line(text: str, speaker: str = "Monica") -> dict:
    return {"type": "line", "speaker": speaker, "direction": None, "text": text}


def reply(*rows: dict) -> str:
    return json.dumps(list(rows))


class TestCandidateLines:
    def test_挑出含目标词的句子(self):
        items = [line("Grab a spoon and eat it now."),
                 line("Nothing at all happened here today.")]
        out = candidate_lines(items, {"spoon"})
        assert len(out) == 1
        assert out[0]["key_words"] == ["spoon"]

    def test_太短的句子不要(self):
        assert candidate_lines([line("Grab it.")], {"grab"}) == []

    def test_太长的句子不要(self):
        long = "spoon " + "word " * 20
        assert candidate_lines([line(long)], {"spoon"}) == []

    def test_剥掉舞台提示后再判长度(self):
        """括号里的内容不算句子的一部分。"""
        items = [line("Grab a spoon now. (She walks over to the big kitchen.)")]
        out = candidate_lines(items, {"spoon"})
        assert len(out) == 1
        assert "kitchen" not in out[0]["text"]

    def test_保留说话人(self):
        items = [line("Grab a spoon and eat.", speaker="Joey")]
        assert candidate_lines(items, {"spoon"})[0]["speaker"] == "Joey"

    def test_忽略非台词(self):
        items = [{"type": "scene", "text": "Central Perk with a spoon here"}]
        assert candidate_lines(items, {"spoon"}) == []

    def test_多个目标词都记录(self):
        items = [line("Grab a spoon and a bowl please.")]
        assert candidate_lines(items, {"spoon", "bowl"})[0]["key_words"] \
            == ["bowl", "spoon"]


class TestExtract:
    def test_句子文本用原话不用_LLM_返回的(self):
        """环节 9 要放原片音轨，改写过的句子对不上语音。"""
        src = "Grab a spoon and eat it."
        llm = FakeLLM([reply({
            "text": src, "meaning_zh": "拿个勺子吃吧",
            "chunks": [{"id": "grab_a", "text": "Grab a", "meaning_zh": "拿一个"}],
        })])
        sents, _ = extract([{"text": src, "speaker": "Joey",
                             "key_words": ["spoon"]}], llm)
        assert sents[0].text == src
        assert sents[0].meaning_zh == "拿个勺子吃吧"

    def test_chunk_必须出现在原句里(self):
        """LLM 拼凑的 chunk 要丢掉。"""
        src = "Grab a spoon and eat."
        llm = FakeLLM([reply({
            "text": src, "meaning_zh": "x",
            "chunks": [
                {"id": "grab_a", "text": "Grab a", "meaning_zh": "拿一个"},
                {"id": "fake", "text": "never appeared", "meaning_zh": "假的"},
            ],
        })])
        _, chunks = extract([{"text": src, "key_words": ["spoon"]}], llm)
        assert [c.id for c in chunks] == ["grab_a"]

    def test_chunk_长度窗口(self):
        src = "I am really going to grab a spoon now."
        llm = FakeLLM([reply({
            "text": src, "meaning_zh": "x",
            "chunks": [
                {"id": "too_short", "text": "I", "meaning_zh": "我"},
                {"id": "ok", "text": "grab a spoon", "meaning_zh": "拿勺子"},
                {"id": "too_long", "text": "I am really going to grab a spoon",
                 "meaning_zh": "长"},
            ],
        })])
        _, chunks = extract([{"text": src, "key_words": ["spoon"]}], llm)
        assert [c.id for c in chunks] == ["ok"]

    def test_chunk_跨句去重(self):
        a, b = "It is kind of nice here.", "That was kind of weird then."
        llm = FakeLLM([reply(
            {"text": a, "meaning_zh": "x",
             "chunks": [{"id": "kind_of", "text": "kind of", "meaning_zh": "有点"}]},
            {"text": b, "meaning_zh": "y",
             "chunks": [{"id": "kind_of", "text": "kind of", "meaning_zh": "有点"}]},
        )])
        sents, chunks = extract(
            [{"text": a, "key_words": ["nice"]},
             {"text": b, "key_words": ["weird"]}], llm)
        assert len(chunks) == 1, "同一个 chunk 只存一份"
        assert all("kind_of" in s.chunks for s in sents)

    def test_句子_id_递增(self):
        a, b = "Grab a spoon now please.", "Take a bowl now please."
        llm = FakeLLM([reply(
            {"text": a, "meaning_zh": "x", "chunks": []},
            {"text": b, "meaning_zh": "y", "chunks": []},
        )])
        sents, _ = extract([{"text": a, "key_words": ["spoon"]},
                            {"text": b, "key_words": ["bowl"]}], llm)
        assert [s.id for s in sents] == ["s01", "s02"]

    def test_covers_words_来自_chunk_文本(self):
        src = "It is kind of nice."
        llm = FakeLLM([reply({
            "text": src, "meaning_zh": "x",
            "chunks": [{"id": "kind_of", "text": "kind of", "meaning_zh": "有点"}],
        })])
        _, chunks = extract([{"text": src, "key_words": ["nice"]}], llm)
        assert chunks[0].covers_words == ["kind", "of"]

    def test_LLM_漏答的句子跳过(self):
        a, b = "Grab a spoon now please.", "Take a bowl now please."
        llm = FakeLLM([reply({"text": a, "meaning_zh": "x", "chunks": []})])
        sents, _ = extract([{"text": a, "key_words": ["spoon"]},
                            {"text": b, "key_words": ["bowl"]}], llm)
        assert [s.text for s in sents] == [a]

    def test_整批失败拆半重试(self):
        a, b = "Grab a spoon now please.", "Take a bowl now please."
        llm = FakeLLM([
            "不是 JSON",
            reply({"text": a, "meaning_zh": "x", "chunks": []}),
            reply({"text": b, "meaning_zh": "y", "chunks": []}),
        ])
        sents, _ = extract([{"text": a, "key_words": ["spoon"]},
                            {"text": b, "key_words": ["bowl"]}], llm, batch=2)
        assert len(sents) == 2
        assert len(llm.calls) == 3

    def test_全失败返回空(self):
        sents, chunks = extract(
            [{"text": "Grab a spoon now.", "key_words": ["spoon"]}], FakeLLM([]))
        assert sents == [] and chunks == []

    def test_空输入(self):
        llm = FakeLLM([])
        assert extract([], llm) == ([], [])
        assert llm.calls == []

    def test_保留_key_words_和说话人(self):
        src = "Grab a spoon and eat."
        llm = FakeLLM([reply({"text": src, "meaning_zh": "x", "chunks": []})])
        sents, _ = extract([{"text": src, "speaker": "Joey",
                             "key_words": ["spoon"]}], llm)
        assert sents[0].speaker == "Joey"
        assert sents[0].key_words == ["spoon"]

    def test_序列化(self):
        src = "Grab a spoon and eat."
        llm = FakeLLM([reply({
            "text": src, "meaning_zh": "拿勺子",
            "chunks": [{"id": "grab_a", "text": "Grab a", "meaning_zh": "拿"}],
        })])
        sents, chunks = extract([{"text": src, "key_words": ["spoon"]}], llm)
        d = sents[0].to_dict()
        assert set(d) == {"id", "text", "meaning_zh", "speaker",
                          "chunks", "key_words"}
        assert set(chunks[0].to_dict()) == {"id", "text", "meaning_zh",
                                           "covers_words"}
        json.dumps({"s": [d], "c": [c.to_dict() for c in chunks]})
