"""素材加载层：读 illit-english-mvp 的 lesson JSON。

NFR-4：数据层零改动。这里只做读取和关系推导，不写回任何文件。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path


@dataclass(frozen=True)
class Word:
    lemma: str
    freq: int
    audio: str
    audio_slow: str
    image: str
    meaning_zh: str
    skip_image: bool = False

    @classmethod
    def from_raw(cls, d: dict) -> Word:
        return cls(
            lemma=d["lemma"],
            freq=d.get("freq", 0),
            audio=d.get("audio", ""),
            audio_slow=d.get("audio_slow", ""),
            image=d.get("image", ""),
            meaning_zh=d.get("meaning_zh", ""),
            skip_image=bool(d.get("skip_image", False)),
        )


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    meaning_zh: str
    audio_tts: str
    audio_tts_slow: str
    image: str
    covers_words: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, d: dict) -> Chunk:
        return cls(
            id=d["id"],
            text=d["text"],
            meaning_zh=d.get("meaning_zh", ""),
            audio_tts=d.get("audio_tts", ""),
            audio_tts_slow=d.get("audio_tts_slow", ""),
            image=d.get("image", ""),
            covers_words=tuple(d.get("covers_words", ())),
        )


@dataclass(frozen=True)
class Sentence:
    id: str
    text: str
    meaning_zh: str
    audio_tts: str
    audio_tts_slow: str
    audio_clip: str
    image: str
    chunk_ids: tuple[str, ...] = ()
    key_words: tuple[str, ...] = ()
    time_start: str = ""
    time_end: str = ""

    @classmethod
    def from_raw(cls, d: dict) -> Sentence:
        return cls(
            id=d["id"],
            # 字段名 text_admin_only 是刻意的：正式界面不展示拼写
            text=d.get("text_admin_only", ""),
            meaning_zh=d.get("meaning_zh", ""),
            audio_tts=d.get("audio_tts", ""),
            audio_tts_slow=d.get("audio_tts_slow", ""),
            audio_clip=d.get("audio_clip", ""),
            image=d.get("image", ""),
            chunk_ids=tuple(d.get("chunks", ())),
            key_words=tuple(d.get("key_words", ())),
            time_start=d.get("time_start", ""),
            time_end=d.get("time_end", ""),
        )


@dataclass
class Episode:
    id: str
    title: str
    level: int
    duration_seconds: int
    words: list[Word]
    chunks: list[Chunk]
    sentences: list[Sentence]
    distractors: dict[str, list[str]] = field(default_factory=dict)

    # ---- 索引 ----

    @cached_property
    def _by_lemma(self) -> dict[str, Word]:
        return {w.lemma: w for w in self.words}

    @cached_property
    def _by_chunk_id(self) -> dict[str, Chunk]:
        return {c.id: c for c in self.chunks}

    @cached_property
    def _by_sentence_id(self) -> dict[str, Sentence]:
        return {s.id: s for s in self.sentences}

    def word(self, lemma: str) -> Word:
        return self._by_lemma[lemma]

    def chunk(self, chunk_id: str) -> Chunk:
        return self._by_chunk_id[chunk_id]

    def sentence(self, sentence_id: str) -> Sentence:
        return self._by_sentence_id[sentence_id]

    def distractors_for(self, lemma: str) -> list[str]:
        return list(self.distractors.get(lemma, []))

    # ---- 覆盖关系（FR-3.2 打包依据）----

    def words_covered_by_sentence(self, sentence_id: str) -> set[str]:
        """一句话教到哪些词 = key_words + 它引用的 chunk 的 covers_words，取词表交集。"""
        s = self.sentence(sentence_id)
        got = set(s.key_words)
        for cid in s.chunk_ids:
            c = self._by_chunk_id.get(cid)
            if c:
                got |= set(c.covers_words)
        return got & set(self._by_lemma)

    def chunks_covering(self, lemmas: set[str]) -> list[Chunk]:
        """哪些 chunk 的词全都在给定词集里 —— 这节课能用的 chunk。"""
        return [
            c for c in self.chunks
            if c.covers_words and set(c.covers_words) <= lemmas
        ]

    def covered_words(self) -> set[str]:
        out: set[str] = set()
        for s in self.sentences:
            out |= self.words_covered_by_sentence(s.id)
        return out

    def tail_words(self) -> set[str]:
        """长尾词：没有任何句子教到它（FR-3.4 作为顺带词）。"""
        return set(self._by_lemma) - self.covered_words()


def load_episode(mvp_root: str | Path, episode_id: str) -> Episode:
    """从 illit-english-mvp 目录加载一集。

    episode_id 形如 "peppa-s01e01"，对应 lesson-peppa-s01e01.json。
    """
    root = Path(mvp_root)
    raw = json.loads((root / f"lesson-{episode_id}.json").read_text())
    return Episode(
        id=raw["id"],
        title=raw.get("title", ""),
        level=raw.get("level", 1),
        duration_seconds=raw.get("duration_seconds", 0),
        words=[Word.from_raw(d) for d in raw.get("words", [])],
        chunks=[Chunk.from_raw(d) for d in raw.get("chunks", [])],
        sentences=[Sentence.from_raw(d) for d in raw.get("sentences", [])],
        distractors=raw.get("distractors", {}),
    )
