"""把剧本里的原始 token 归一化到 CEFR 词表条目上。

机械匹配的天花板：`goodnight` 查不到，但词表里有 `good night`(A1)；
`alright` 查不到，词表里有 `all right`(A1)；`de caff` 是 `decaf` 被空格拆开。
这类判断需要语言常识，交给 LLM，规则只做它擅长的部分。

分两层：
1. `mechanical_pass()` — 词形还原后直查词表，命中的不必花 LLM 的钱
2. `llm_normalize()`   — 只把没命中的送 LLM，让它给出规范形式 + 类别
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from lemminflect import getAllLemmas

from ailesson.infra.llm import BaseLLM, LLMError

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

# token 的归类。teachable 之外的都不进教学池
CATEGORIES = (
    "word",          # 正常单词，能定级
    "contraction",   # I'm / don't / kinda / outta —— 口语缩略或变体
    "proper_noun",   # 人名地名品牌
    "interjection",  # oh / umm / ahh
    "fragment",      # 解析残留：单字母、被拆开的半个词
)

CONTRACTIONS = {
    "i'm": "be", "i've": "have", "i'll": "will", "i'd": "would",
    "you're": "be", "you've": "have", "you'll": "will", "you'd": "would",
    "he's": "be", "she's": "be", "it's": "be", "that's": "be",
    "we're": "be", "we've": "have", "we'll": "will", "we'd": "would",
    "they're": "be", "they've": "have", "they'll": "will", "they'd": "would",
    "there's": "be", "here's": "be", "what's": "be", "who's": "be",
    "don't": "do", "doesn't": "do", "didn't": "do", "isn't": "be",
    "aren't": "be", "wasn't": "be", "weren't": "be", "can't": "can",
    "couldn't": "could", "wouldn't": "would", "shouldn't": "should",
    "won't": "will", "haven't": "have", "hasn't": "have", "hadn't": "have",
    "let's": "let", "ain't": "be",
}


@dataclass
class VocabEntry:
    """一个 token 的归一化结果。"""
    token: str                      # 剧本原样
    count: int                      # 本集出现次数
    lemma: str | None = None        # 规范形式，可能含空格（good night）
    level: str | None = None        # A1..C2，未收录为 None
    category: str = "word"
    source: str = "rule"            # rule | llm
    note: str | None = None

    @property
    def teachable(self) -> bool:
        return self.category in ("word", "contraction") and self.level is not None

    def to_dict(self) -> dict:
        d = {"token": self.token, "count": self.count, "category": self.category,
             "source": self.source}
        if self.lemma and self.lemma != self.token:
            d["lemma"] = self.lemma
        if self.level:
            d["level"] = self.level
        if self.note:
            d["note"] = self.note
        return d


def load_wordlist(cefr_dir: str | Path) -> dict[str, str]:
    """headword → 最低 CEFR 等级。

    词组条目一并保留（`good night` / `all right`），归一化后要靠它们命中。
    """
    out: dict[str, str] = {}
    files = sorted(Path(cefr_dir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"{cefr_dir} 下没有 CEFR 词表 CSV")
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                head = (row.get("headword") or "").strip().lower()
                lv = (row.get("CEFR") or "").strip().upper()[:2]
                if not head or lv not in LEVELS:
                    continue
                if head not in out or LEVELS.index(lv) < LEVELS.index(out[head]):
                    out[head] = lv
    return out


def candidates(token: str) -> list[str]:
    """token 的所有可能查表形式。"""
    forms = {token}
    if token in CONTRACTIONS:
        forms.add(CONTRACTIONS[token])
    if "'" in token:
        forms.add(token.split("'")[0])          # monica's → monica
        forms.add(token.replace("'", ""))       # y'know → yknow
    for group in getAllLemmas(token).values():
        forms.update(f.lower() for f in group)
    return [f for f in forms if f]


def lookup(token: str, wordlist: dict[str, str]) -> tuple[str, str] | None:
    """返回 (规范形式, 等级)，查不到给 None。取最易的等级。"""
    best: tuple[str, str] | None = None
    for form in candidates(token):
        lv = wordlist.get(form)
        if lv and (best is None or LEVELS.index(lv) < LEVELS.index(best[1])):
            best = (form, lv)
    return best


def mechanical_pass(
    freq: dict[str, int], wordlist: dict[str, str]
) -> tuple[list[VocabEntry], list[VocabEntry]]:
    """规则能定的先定掉，返回 (已定级, 待 LLM 处理)。"""
    resolved: list[VocabEntry] = []
    pending: list[VocabEntry] = []

    for token, n in sorted(freq.items(), key=lambda kv: -kv[1]):
        hit = lookup(token, wordlist)
        if hit:
            lemma, lv = hit
            resolved.append(VocabEntry(
                token=token, count=n, lemma=lemma, level=lv,
                category="contraction" if token in CONTRACTIONS else "word",
            ))
        else:
            pending.append(VocabEntry(token=token, count=n))
    return resolved, pending


SYSTEM = """你在给英语教学系统整理词表。输入是美剧台词里切出来的 token，
它们在 CEFR-J 词表里直接查不到。你要判断每个 token 真正对应什么。

CEFR-J 词表的特点：只收基础词形，派生词和复合词不单独列条目，
但**词组条目是有的**（"good night" 在 A1，"all right" 在 A1）。
所以 goodnight 应还原成 good night，alright 应还原成 all right。

对每个 token 给出：
- lemma: 规范形式。复合词该带空格就带空格；口语变体还原成标准拼写
  （kinda→kind of, outta→out of, gonna→going to, lemme→let me,
  gimme→give me, ya→you, umm→um, ohh→oh, rach→Rachel）；
  解析残留的碎片（单字母、被空格拆开的半个词）尽量拼回完整词
  （"de"+"caff" 属于 decaf）。判断不了就填 null。
- category: word | contraction | proper_noun | interjection | fragment
  - word: 正常实义词
  - contraction: 口语缩略/非正式变体
  - proper_noun: 人名、地名、品牌、作品名
  - interjection: 语气词、拟声词
  - fragment: 解析残留，不是完整词
- level: 你估计的 CEFR 等级 A1/A2/B1/B2/C1/C2。这是兜底用的，
  只在你认为该词确实属于英语常用词汇时给；专有名词、碎片、
  剧集专属造词一律填 null。

只输出 JSON 数组，不要解释：
[{"token":"goodnight","lemma":"good night","category":"word","level":"A1"}]"""


def _chunks(items: list[VocabEntry], size: int) -> Iterable[list[VocabEntry]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def llm_normalize(
    pending: list[VocabEntry],
    wordlist: dict[str, str],
    llm: BaseLLM,
    *,
    batch: int = 60,
    context: str | None = None,
) -> list[VocabEntry]:
    """把机械匹配漏掉的 token 交给 LLM 归一化，再回查词表。

    LLM 给出的 lemma 若能查到词表，用词表等级（权威）；查不到才用
    LLM 估的等级，并标 source=llm 以便人工复核。
    """
    if not pending:
        return []

    out: list[VocabEntry] = []
    for group in _chunks(pending, batch):
        tokens = [e.token for e in group]
        prompt = json.dumps(tokens, ensure_ascii=False)
        if context:
            prompt = f"这些 token 来自：{context}\n\n{prompt}"

        try:
            data = llm.complete_json(prompt, system=SYSTEM, max_tokens=4096)
        except LLMError:
            out.extend(group)                    # 这批留原样，不阻断整体
            continue

        by_token = {}
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("token"):
                    by_token[str(row["token"]).lower()] = row

        for entry in group:
            row = by_token.get(entry.token)
            if not row:
                out.append(entry)
                continue

            lemma = (row.get("lemma") or "").strip().lower() or None
            entry.lemma = lemma
            entry.category = (row.get("category") or "word").strip()
            if entry.category not in CATEGORIES:
                entry.category = "word"
            entry.source = "llm"

            # 归一化后回查词表：词表说了算
            hit = wordlist.get(lemma) if lemma else None
            if not hit and lemma:
                found = lookup(lemma, wordlist)
                if found:
                    entry.lemma, hit = found
            if hit:
                entry.level = hit
                entry.note = "llm 归一化后命中词表"
            else:
                lv = (row.get("level") or "").strip().upper()[:2]
                entry.level = lv if lv in LEVELS else None
                if entry.level:
                    entry.note = "llm 估级，词表未收"
            out.append(entry)
    return out


WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# 台词里内嵌的舞台提示：Ross: (愣住) Hi. / [Scene: ...] 漏进 text 的残留
STAGE_INLINE_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def spoken_only(text: str) -> str:
    """剥掉台词里内嵌的舞台提示，只留真正说出口的话。

    为什么必须剥：提示里的词学习者在剧里根本听不到，做成词卡没意义。
    实测 S1E1 有 41 个词只出现在提示里（pot / cheer / collapses / massaging），
    不剥的话它们会混进教学池，还会抢占生图配额。
    """
    return STAGE_INLINE_RE.sub(" ", text)


def token_freq(texts: Iterable[str], *, spoken: bool = True) -> dict[str, int]:
    """统计词频。spoken=True 时先剥掉内嵌舞台提示。"""
    if spoken:
        texts = (spoken_only(t) for t in texts)
    freq: dict[str, int] = {}
    for t in texts:
        for w in WORD_RE.findall(t):
            w = w.lower()
            freq[w] = freq.get(w, 0) + 1
    return freq


@dataclass
class VocabProfile:
    """一集的词汇分级结果。"""
    episode_id: str
    tokens: int
    types: int
    entries: list[VocabEntry] = field(default_factory=list)

    def by_level(self) -> dict[str, list[VocabEntry]]:
        out: dict[str, list[VocabEntry]] = {lv: [] for lv in LEVELS}
        for e in self.entries:
            if e.level:
                out[e.level].append(e)
        return {k: v for k, v in out.items() if v}

    def by_category(self) -> dict[str, list[VocabEntry]]:
        out: dict[str, list[VocabEntry]] = {}
        for e in self.entries:
            out.setdefault(e.category, []).append(e)
        return out

    @property
    def unresolved(self) -> list[VocabEntry]:
        return [e for e in self.entries if e.level is None
                and e.category in ("word", "contraction")]

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "tokens": self.tokens,
            "types": self.types,
            "entries": [e.to_dict() for e in self.entries],
        }


def build_profile(
    episode_id: str,
    texts: Iterable[str],
    wordlist: dict[str, str],
    llm: BaseLLM | None = None,
    *,
    context: str | None = None,
) -> VocabProfile:
    freq = token_freq(texts)
    resolved, pending = mechanical_pass(freq, wordlist)
    if llm is not None:
        pending = llm_normalize(pending, wordlist, llm, context=context)
    entries = sorted(resolved + pending, key=lambda e: -e.count)
    return VocabProfile(
        episode_id=episode_id,
        tokens=sum(freq.values()),
        types=len(freq),
        entries=entries,
    )
