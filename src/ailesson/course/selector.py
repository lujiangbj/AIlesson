"""按学习者的单词掌握情况，动态挑该练的 chunk 和句子。

设计纠正（用户 2026-08-14 指出）：
早先把 chunk/句子在生产阶段就定死，并且用「句子覆盖了多少词」当质量指标。
两处都错：

1. **覆盖率不是目标**。词只要在 chunk 或句子里被练到就行，不必人人有份，
   更不必用覆盖率去衡量素材好坏。追这个指标会逼着选一堆平淡的句子。
2. **选句该看教学价值，不是看覆盖**。要挑有重难点的句子——生词密度高、
   有地道口语结构、有连读弱读的。
3. **chunk/句子不该固定**。同一集，词汇量大的人和小白该练不同的句子。
   所以挑选发生在**勾选之后**，按各人的待学词池动态算。

打分口径：难点优先，但要能被当前的待学词池"接住"——句子里全是生词
学不动，全是熟词又没价值。所以是个带惩罚项的加权分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ailesson.contract.episode import Episode

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# 一句里待学词的理想个数。太少没教学价值，太多学不动
IDEAL_NEW = 2
MAX_NEW = 4

# 地道口语结构：有这些说明句子有嚼头，值得练
IDIOM_CUES = (
    "gonna", "wanna", "gotta", "kinda", "sorta", "outta", "lemme", "gimme",
    "ain't", "y'know", "c'mon", "'em", "supposed to", "kind of", "sort of",
    "a bunch of", "no way", "come on", "shut up", "how come", "what if",
    "used to", "would've", "could've", "should've", "let's",
)
# 缩略形式，连读弱读的主要来源
CONTRACTION_RE = re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.I)


@dataclass
class Scored:
    id: str
    score: float
    new_words: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "score": round(self.score, 2),
                "new_words": self.new_words, "reasons": self.reasons}


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def score_sentence(
    text: str,
    unknown_words: set[str],
    *,
    chunk_ids: tuple[str, ...] = (),
    unknown_chunks: set[str] | None = None,
) -> Scored:
    """给一句打教学价值分。

    加分：含待学词（到 IDEAL_NEW 为止）、含待学 chunk、有地道口语结构、
    有缩略形式（连读练习点）、长度适中。
    扣分：待学词过多（学不动）、一个待学点都没有（没价值）。
    """
    toks = _tokens(text)
    if not toks:
        return Scored(id="", score=0.0)

    new = sorted({t for t in toks if t in unknown_words})
    uc = set(chunk_ids) & (unknown_chunks or set())
    reasons: list[str] = []
    score = 0.0

    # 待学词：前 IDEAL_NEW 个每个 3 分，之后递减，超过 MAX_NEW 开始扣
    for i, _ in enumerate(new):
        if i < IDEAL_NEW:
            score += 3.0
        elif i < MAX_NEW:
            score += 1.0
        else:
            score -= 1.5
    if new:
        reasons.append(f"{len(new)}个待学词")
    if len(new) > MAX_NEW:
        reasons.append("生词过密")

    # 待学 chunk：句子是 chunk 的天然载体
    if uc:
        score += 2.5 * len(uc)
        reasons.append(f"{len(uc)}个待学短语")

    # 地道口语结构
    low = text.lower()
    hits = [c for c in IDIOM_CUES if c in low]
    if hits:
        score += min(2.0, 0.8 * len(hits))
        reasons.append(f"口语结构({hits[0]})")

    # 缩略形式 = 连读弱读练习点
    n_contr = len(CONTRACTION_RE.findall(text))
    if n_contr:
        score += min(1.5, 0.5 * n_contr)
        reasons.append(f"{n_contr}处缩略")

    # 长度：5~12 词最适合跟读
    n = len(toks)
    if 5 <= n <= 12:
        score += 1.0
    elif n < 4:
        score -= 1.5
        reasons.append("过短")
    elif n > 16:
        score -= 1.0
        reasons.append("过长")

    # 一个待学点都没有：纯复习句，价值低
    if not new and not uc:
        score -= 2.0
        reasons.append("无待学点")

    return Scored(id="", score=score, new_words=new, reasons=reasons)


def _rank_sentences(
    sentences,
    unknown_words: set[str],
    unknown_chunks: set[str],
    *,
    limit: int = 24,
    min_score: float = 3.0,
) -> list[Scored]:
    """给一批句子打分排序。min_score 以下的丢掉。"""
    out: list[Scored] = []
    for s in sentences:
        sc = score_sentence(s.text, unknown_words,
                            chunk_ids=s.chunk_ids, unknown_chunks=unknown_chunks)
        if sc.score < min_score:
            continue
        sc.id = s.id
        out.append(sc)
    # 高分优先，同分按剧情顺序（id 递增）
    out.sort(key=lambda x: (-x.score, x.id))
    return out[:limit]


def pick_sentences(
    ep: Episode,
    unknown_words: set[str],
    unknown_chunks: set[str],
    *,
    limit: int = 24,
    min_score: float = 3.0,
) -> list[Scored]:
    """按教学价值挑句子。

    limit 控制规模——PRD 每节 2~3 句，24 句够 8~12 节。挑太多会把
    课程数撑爆（早先 76 句全进池，打包出 43 节）。
    """
    return _rank_sentences(ep.sentences, unknown_words, unknown_chunks,
                           limit=limit, min_score=min_score)


def _rank_chunks(
    chunks,
    ep: Episode,
    unknown_words: set[str],
    picked_sentence_ids: set[str],
    *,
    limit: int = 30,
) -> list[Scored]:
    """给一批 chunk 打分排序。零分及以下丢掉。"""
    in_picked: set[str] = set()
    for sid in picked_sentence_ids:
        try:
            in_picked |= set(ep.sentence(sid).chunk_ids)
        except KeyError:
            continue

    # chunk 被多少句引用 = 复用度
    use_count: dict[str, int] = {}
    for s in ep.sentences:
        for cid in s.chunk_ids:
            use_count[cid] = use_count.get(cid, 0) + 1

    out: list[Scored] = []
    for c in chunks:
        new = sorted(set(c.covers_words) & unknown_words)
        score, reasons = 0.0, []
        if c.id in in_picked:
            score += 4.0
            reasons.append("入选句子里出现")
        if new:
            score += 2.0 * min(len(new), 2)
            reasons.append(f"{len(new)}个待学词")
        reuse = use_count.get(c.id, 0)
        if reuse >= 2:
            score += 1.5
            reasons.append(f"复用{reuse}次")
        low = c.text.lower()
        if any(k in low for k in IDIOM_CUES):
            score += 1.5
            reasons.append("地道搭配")
        if score <= 0:
            continue
        out.append(Scored(id=c.id, score=score, new_words=new, reasons=reasons))

    out.sort(key=lambda x: (-x.score, x.id))
    return out[:limit]


def pick_chunks(
    ep: Episode,
    unknown_words: set[str],
    picked_sentence_ids: set[str],
    *,
    limit: int = 30,
) -> list[Scored]:
    """挑该练的 chunk。

    优先级：被选中的句子引用到的 chunk（句子是它的语境载体）> 含待学词的 >
    高频复用的。不追求覆盖所有待学词——词在别处也能练到。
    """
    return _rank_chunks(ep.chunks, ep, unknown_words, picked_sentence_ids,
                        limit=limit)


def build_pool(
    ep: Episode,
    unknown_words: set[str],
    *,
    sentence_limit: int = 24,
    chunk_limit: int = 30,
    restrict_sentences: set[str] | None = None,
    restrict_chunks: set[str] | None = None,
) -> dict[str, list[Scored]]:
    """算出该练的 chunk 和句子。

    两轮：先用待学词挑句子（此时还不知道该练哪些 chunk），
    再用入选句子反推 chunk，最后用新 chunk 集重挑一遍句子——
    因为 chunk 会影响句子得分。

    restrict_* 给听力探测（probe.py）实测出的"听不懂"集合。给了就只在
    这个范围内挑——探测是测量，比这里的启发式打分可信；打分只负责
    在其中按教学价值排序限量。不给则全集参与（纯启发式兜底）。
    """
    sents_all = ep.sentences
    if restrict_sentences is not None:
        sents_all = [s for s in ep.sentences if s.id in restrict_sentences]
    chunks_all = ep.chunks
    if restrict_chunks is not None:
        chunks_all = [c for c in ep.chunks if c.id in restrict_chunks]

    first = _rank_sentences(sents_all, unknown_words, set(),
                            limit=sentence_limit * 2)
    chunks = _rank_chunks(chunks_all, ep, unknown_words,
                          {s.id for s in first}, limit=chunk_limit)
    cids = {c.id for c in chunks}
    sents = _rank_sentences(sents_all, unknown_words, cids,
                            limit=sentence_limit)
    # 句子重挑后，把没被任何入选句引用、又不含待学词的 chunk 去掉
    in_sents = {x for s in sents for x in ep.sentence(s.id).chunk_ids}
    keep_c = [c for c in chunks if c.new_words or c.id in in_sents]
    return {"sentences": sents, "chunks": keep_c}
