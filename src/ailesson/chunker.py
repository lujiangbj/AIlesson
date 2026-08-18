"""从剧本抽教学短语（chunk）和教学句（sentence）。

对齐 MVP lesson JSON 的结构（illit-english-mvp/lesson-peppa-s01e01.json）：
- chunk: {id, text, meaning_zh, covers_words}   —— 2~5 词的可复用语块
- sentence: {id, text, meaning_zh, chunks, key_words}

为什么必须有这一层：PRD FR-3.2 明确「打包单位是句子，不是词」，
环节 6/7 要 chunk 听辨和跟读，环节 9/10 要句子原声和跟读。只做词那层，
L2/L3 环节会空转。

选句原则（喂给 LLM 的约束）：句子必须来自剧中原话，不许改写——
环节 9 要放原片音轨，改写过的句子对不上口型和语音。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from .llm import BaseLLM, LLMError
from .vocab_cefr import WORD_RE, spoken_only

# 句子长度窗口：太短没教学价值，太长跟读吃力
MIN_SENT_WORDS = 4
MAX_SENT_WORDS = 14
# chunk 长度窗口
MIN_CHUNK_WORDS = 2
MAX_CHUNK_WORDS = 5


@dataclass
class Chunk:
    id: str
    text: str
    meaning_zh: str = ""
    covers_words: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text,
                "meaning_zh": self.meaning_zh,
                "covers_words": self.covers_words}


@dataclass
class Sentence:
    id: str
    text: str
    meaning_zh: str = ""
    speaker: str | None = None
    chunks: list[str] = field(default_factory=list)
    key_words: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text,
                "meaning_zh": self.meaning_zh, "speaker": self.speaker,
                "chunks": self.chunks, "key_words": self.key_words}


def candidate_lines(items: list[dict], target_words: set[str]) -> list[dict]:
    """挑出适合当教学句的台词。

    条件：长度落在窗口内、含至少一个目标生词、剥掉舞台提示后仍完整。
    """
    out = []
    for it in items:
        if it.get("type") != "line":
            continue
        text = re.sub(r"\s+", " ", spoken_only(it["text"])).strip()
        if not text:
            continue
        words = [w.lower() for w in WORD_RE.findall(text)]
        if not (MIN_SENT_WORDS <= len(words) <= MAX_SENT_WORDS):
            continue
        hits = sorted(set(words) & target_words)
        if not hits:
            continue
        out.append({"text": text, "speaker": it.get("speaker"),
                    "key_words": hits})
    return out


SYSTEM = """你在把美剧台词整理成英语教学素材。给你一批**剧中原话**，
为每句拆出可复用的语块（chunk），并给出中文意思。

硬规则：
1. **句子文本必须一字不改地沿用原话**。教学时要放原片音轨，改写过的句子
   对不上语音。你只做拆分和翻译，不做润色。
2. chunk 是 2~5 个词的**可复用语块**，比如 "have a cup of coffee" 里的
   "a cup of"、"I'm telling you"、"kind of"。要求：
   · 必须是原句里连续出现的片段，不许拼凑
   · 有独立语义，能迁移到别的句子里用
   · 优先选固定搭配、动词短语、口语套话
   · 一句拆 1~3 个，太碎没价值
3. meaning_zh 是自然的中文口语翻译，不要直译腔。
4. chunk 的 id 用小写字母和下划线，取自其文本（have_a_cup_of）。

只输出 JSON 数组，每个元素对应输入的一句：
[{"text":"原句原样","meaning_zh":"中文意思",
  "chunks":[{"id":"kind_of","text":"kind of","meaning_zh":"有点"}]}]"""


def _chunks_of(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:40] or "chunk"


def extract(
    lines: list[dict],
    llm: BaseLLM,
    *,
    batch: int = 8,
    context: str | None = None,
    max_tokens: int = 16384,
) -> tuple[list[Sentence], list[Chunk]]:
    """把候选台词交给 LLM 拆 chunk 并翻译。

    batch 小一点：每句要回中文翻译 + 若干 chunk，很吃 token。
    整批失败会拆半重试（同 pickable 的做法）。
    """
    sentences: list[Sentence] = []
    chunk_pool: dict[str, Chunk] = {}
    sid = 0

    for group in _chunks_of(lines, batch):
        data = _ask(group, llm, context, max_tokens)
        if data is None and len(group) > 1:
            mid = len(group) // 2
            halves = [_ask(group[:mid], llm, context, max_tokens),
                      _ask(group[mid:], llm, context, max_tokens)]
            data = [r for h in halves if h for r in h] or None
        if data is None:
            continue

        by_text = {}
        for row in data:
            if isinstance(row, dict) and row.get("text"):
                by_text[str(row["text"]).strip().lower()] = row

        for src in group:
            row = by_text.get(src["text"].strip().lower())
            if not row:
                continue
            sid += 1
            sent = Sentence(
                id=f"s{sid:02d}",
                text=src["text"],                 # 用原话，不用 LLM 回的
                meaning_zh=(row.get("meaning_zh") or "").strip(),
                speaker=src.get("speaker"),
                key_words=src.get("key_words", []),
            )
            for c in (row.get("chunks") or []):
                if not isinstance(c, dict):
                    continue
                ctext = (c.get("text") or "").strip()
                if not ctext:
                    continue
                n = len(WORD_RE.findall(ctext))
                if not (MIN_CHUNK_WORDS <= n <= MAX_CHUNK_WORDS):
                    continue
                # chunk 必须真的出现在原句里，否则是 LLM 拼的
                if ctext.lower() not in src["text"].lower():
                    continue
                cid = (c.get("id") or "").strip().lower() or _slug(ctext)
                cid = re.sub(r"[^a-z0-9_]+", "_", cid)[:40]
                if cid not in chunk_pool:
                    covers = [w.lower() for w in WORD_RE.findall(ctext)]
                    chunk_pool[cid] = Chunk(
                        id=cid, text=ctext,
                        meaning_zh=(c.get("meaning_zh") or "").strip(),
                        covers_words=covers,
                    )
                if cid not in sent.chunks:
                    sent.chunks.append(cid)
            sentences.append(sent)

    return sentences, list(chunk_pool.values())


def _ask(group: list[dict], llm: BaseLLM, context: str | None,
         max_tokens: int) -> list | None:
    payload = [{"text": g["text"]} for g in group]
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    if context:
        prompt = f"这些台词来自：{context}\n\n{prompt}"
    try:
        data = llm.complete_json(prompt, system=SYSTEM, max_tokens=max_tokens)
    except LLMError:
        return None
    return data if isinstance(data, list) else None
