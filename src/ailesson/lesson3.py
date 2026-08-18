"""三层课程运行时：词 / 短语 / 句子都是一等教学点。

替代 lesson.py 的「短语句子作为词的配套」模式。CET-6 用户的一节课可能一个生词
都没有，全是短语和句子 —— 那也必须能正常跑完，且每个点都有完整的曝光形态。

每个教学点的曝光：听辨 → 反向 → 跟读（+ 句子还会在盲听里再听一遍）。
课内仍不产生掌握（每方向只 1 次），沿用 lesson.py 的诚实原则。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .cards import Card, _choices_for_word
from .episode import Episode
from .packer3 import LessonSpec3
from .progress import Progress

MAX_REVIEW = 9          # 复习点上限（三层合计）
MAX_SPOT_CHECK = 3


class Seg3(Enum):
    REVIEW = "review"
    SPOT_CHECK = "spot_check"
    WORD_A2I = "word_a2i"
    WORD_I2A = "word_i2a"
    WORD_SHADOW = "word_shadow"
    CHUNK_A2I = "chunk"
    CHUNK_I2A = "chunk_i2a"
    CHUNK_SHADOW = "chunk_shadow"
    INTERLUDE = "interlude"
    SENTENCE_A2I = "sentence"
    SENTENCE_I2A = "sentence_i2a"
    SENTENCE_SHADOW = "sentence_shadow"
    MIXED = "mixed"
    REDO = "redo"
    BLIND_LISTEN = "blind_listen"
    REPORT = "report"


@dataclass(frozen=True)
class Segment3:
    index: int
    kind: Seg3
    title: str
    minutes: float


SEGMENTS3: tuple[Segment3, ...] = (
    Segment3(1, Seg3.REVIEW, "开场 + 复习", 3.0),
    Segment3(2, Seg3.SPOT_CHECK, "抽检", 1.0),
    Segment3(3, Seg3.WORD_A2I, "生词首触", 2.0),
    Segment3(4, Seg3.WORD_I2A, "生词反向", 1.5),
    Segment3(5, Seg3.WORD_SHADOW, "生词跟读", 1.5),
    Segment3(6, Seg3.CHUNK_A2I, "短语听辨", 3.0),
    Segment3(7, Seg3.CHUNK_I2A, "短语反向", 2.0),
    Segment3(8, Seg3.CHUNK_SHADOW, "短语跟读", 2.5),
    Segment3(9, Seg3.INTERLUDE, "中场", 1.0),
    Segment3(10, Seg3.SENTENCE_A2I, "句子原声", 3.5),
    Segment3(11, Seg3.SENTENCE_I2A, "句子反向", 2.0),
    Segment3(12, Seg3.SENTENCE_SHADOW, "句子跟读", 3.0),
    Segment3(13, Seg3.MIXED, "混打", 2.0),
    Segment3(14, Seg3.REDO, "错题重做", 1.5),
    Segment3(15, Seg3.BLIND_LISTEN, "场景盲听", 1.5),
    Segment3(16, Seg3.REPORT, "收尾报告", 1.0),
)
SEG3_BY_INDEX = {s.index: s for s in SEGMENTS3}

# 计 streak 的环节：首触和反向。跟读练产出、混打重做是巩固，都不计。
STREAK_SEGMENTS = {1, 2, 3, 4, 6, 7, 10, 11}


@dataclass
class LessonRuntime3:
    episode_id: str
    lesson_index: int
    cards: list[Card]
    progress: Progress
    cursor: int = 0
    stats: dict[str, int] = field(
        default_factory=lambda: {"asked": 0, "correct": 0, "wrong": 0,
                                 "first_try_correct": 0}
    )
    wrong_items: list[tuple[str, str]] = field(default_factory=list)  # (domain, id)
    demoted: list[tuple[str, str]] = field(default_factory=list)
    blind_listen_score: int | None = None
    _answered: dict[str, bool] = field(default_factory=dict)
    _redo_built: bool = False
    _proto: dict[str, Card] = field(default_factory=dict)   # id → 首触卡，重做用
    # 卡序的两个可变输入，落盘以保证续上时能重建出同一副牌
    review_picked: dict[str, list[str]] = field(default_factory=dict)
    spot_picked: dict[str, list[str]] = field(default_factory=dict)
    dir_picked: dict[str, str] = field(default_factory=dict)   # "域:id" → a2i/i2a

    @classmethod
    def build(
        cls, ep: Episode, spec: LessonSpec3, progress: Progress,
        known: dict[str, list[str]] | None = None,
        review: dict[str, list[str]] | None = None,
        dirs: dict[str, str] | None = None,
    ) -> LessonRuntime3:
        cards, proto, picked = _build_cards3(
            ep, spec, progress, known or {}, review, dirs
        )
        return cls(
            episode_id=ep.id, lesson_index=spec.index,
            cards=cards, progress=progress, _proto=proto,
            review_picked=picked["review"], spot_picked=picked["spot"],
            dir_picked=picked["dirs"],
        )

    @classmethod
    def restore(
        cls, ep: Episode, spec: LessonSpec3, progress: Progress,
        snap: dict[str, Any], known: dict[str, list[str]] | None = None,
    ) -> LessonRuntime3:
        # 复习/抽检的选择必须从快照读回，不能重新挑：pick_review 的排序依赖
        # last_at 和 streak，上课过程中它们已经变了，重挑会得到不同的卡序 →
        # 续上时错位到别的卡。
        rt = cls.build(
            ep, spec, progress,
            known=snap.get("spot_picked") or known,
            review=snap.get("review_picked"),
            dirs=snap.get("dir_picked"),
        )
        rt.cursor = snap.get("cursor", 0)
        rt.stats = dict(snap.get("stats", rt.stats))
        rt.wrong_items = [tuple(x) for x in snap.get("wrong_items", [])]
        rt.demoted = [tuple(x) for x in snap.get("demoted", [])]
        rt.blind_listen_score = snap.get("blind_listen_score")
        rt._answered = dict(snap.get("answered", {}))
        rt._redo_built = bool(snap.get("redo_built", False))
        if rt._redo_built:
            rt._insert_redo()
        return rt

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "lesson_index": self.lesson_index,
            "cursor": self.cursor,
            "stats": self.stats,
            "wrong_items": [list(x) for x in self.wrong_items],
            "demoted": [list(x) for x in self.demoted],
            "blind_listen_score": self.blind_listen_score,
            "answered": self._answered,
            "redo_built": self._redo_built,
            "review_picked": self.review_picked,
            "spot_picked": self.spot_picked,
            "dir_picked": self.dir_picked,
        }

    # ---- 推进 ----

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.cards)

    def current(self) -> Card | None:
        if self.finished:
            return None
        card = self.cards[self.cursor]
        if card.segment_index == 14 and not self._redo_built:
            self._redo_built = True
            self._insert_redo()
            if self.finished:
                return None
            card = self.cards[self.cursor]
        return card

    def advance(self) -> None:
        if not self.finished:
            self.cursor += 1

    def answer(self, correct: bool, at: int = 0) -> None:
        card = self.current()
        if card is None:
            return
        if not card.needs_answer:
            self.advance()
            return
        at = at or int(time.time())

        self.stats["asked"] += 1
        self.stats["correct" if correct else "wrong"] += 1

        key = f"{card.domain}:{card.item_id}"
        if card.segment_index in (3, 6, 10) and key not in self._answered:
            self._answered[key] = correct
            if correct and not card.is_bonus:
                self.stats["first_try_correct"] += 1

        direction = "i2a" if card.kind == "i2a" else "a2i"
        if card.segment_index in STREAK_SEGMENTS:
            if card.kind in ("a2i", "i2a", "chunk", "sentence"):
                self.progress.record(card.domain, card.item_id, direction, correct, at)
        elif not correct:
            self.progress.record(card.domain, card.item_id, direction, False, at)

        if not correct:
            pair = (card.domain, card.item_id)
            if pair not in self.wrong_items:
                self.wrong_items.append(pair)
            if card.segment_index == 2 and pair not in self.demoted:
                self.demoted.append(pair)
        self.advance()

    def self_assess(self, score: int) -> None:
        card = self.current()
        if card is not None and card.kind == "assess":
            self.blind_listen_score = score
            self.advance()

    def _insert_redo(self) -> None:
        for c in [x for x in self.cards if x.segment_index == 14]:
            self.cards.remove(c)
        if not self.wrong_items:
            return
        at = next((i for i, c in enumerate(self.cards) if c.segment_index > 14),
                  len(self.cards))
        # 按教学顺序重做，不按错误发现顺序
        order = list(self._proto)
        todo = sorted(
            (f"{d}:{i}" for d, i in self.wrong_items),
            key=lambda k: order.index(k) if k in order else len(order),
        )
        new = []
        for k in todo:
            proto = self._proto.get(k)
            if not proto:
                continue
            new.append(
                Card(
                    card_id=f"redo:{k}", segment_index=14, kind=proto.kind,
                    domain=proto.domain, item_id=proto.item_id,
                    target_words=proto.target_words,
                    prompt_audio=proto.prompt_audio,
                    prompt_audio_slow=proto.prompt_audio_slow,
                    image=proto.image, meaning_zh=proto.meaning_zh,
                    text=proto.text, choices=proto.choices,
                    correct_id=proto.correct_id,
                )
            )
        self.cards[at:at] = new


def _build_cards3(
    ep: Episode, spec: LessonSpec3, progress: Progress,
    known: dict[str, list[str]], review_in: dict[str, list[str]] | None = None,
    dirs_in: dict[str, str] | None = None,
) -> tuple[list[Card], dict[str, Card], dict[str, Any]]:
    cards: list[Card] = []
    proto: dict[str, Card] = {}

    words = list(spec.focus_words)
    chunks = list(spec.chunk_ids)
    sents = list(spec.sentence_ids)

    def word_card(seg: int, kind: str, lemma: str, bonus: bool = False) -> Card:
        w = ep.word(lemma)
        return Card(
            card_id=f"s{seg}:{kind}:{lemma}", segment_index=seg, kind=kind,
            domain="words", item_id=lemma, target_words=(lemma,),
            prompt_audio=w.audio, prompt_audio_slow=w.audio_slow,
            image="" if w.skip_image else w.image, meaning_zh=w.meaning_zh,
            text=lemma, choices=_choices_for_word(ep, lemma, words),
            correct_id=lemma, is_bonus=bonus,
        )

    def chunk_card(seg: int, kind: str, cid: str, pool: list[str],
                   bonus: bool = False) -> Card:
        c = ep.chunk(cid)
        others = [x for x in pool if x != cid] or [x.id for x in ep.chunks if x.id != cid]
        return Card(
            card_id=f"s{seg}:{kind}:{cid}", segment_index=seg, kind=kind,
            domain="chunks", item_id=cid, target_words=tuple(c.covers_words),
            prompt_audio=c.audio_tts, prompt_audio_slow=c.audio_tts_slow,
            image=c.image, meaning_zh=c.meaning_zh, text=c.text,
            choices=tuple([cid, *others[:3]]), correct_id=cid, is_bonus=bonus,
        )

    def sent_card(seg: int, kind: str, sid: str, pool: list[str],
                  bonus: bool = False) -> Card:
        s = ep.sentence(sid)
        others = [x for x in pool if x != sid] or [x.id for x in ep.sentences if x.id != sid]
        return Card(
            card_id=f"s{seg}:{kind}:{sid}", segment_index=seg, kind=kind,
            domain="sentences", item_id=sid,
            target_words=tuple(ep.words_covered_by_sentence(sid)),
            # 句子用原片切片，这是「听懂真实语流」的核心
            prompt_audio=s.audio_clip or s.audio_tts,
            prompt_audio_slow=s.audio_tts_slow,
            image=s.image, meaning_zh=s.meaning_zh, text=s.text,
            choices=tuple([sid, *others[:3]]), correct_id=sid, is_bonus=bonus,
        )

    # 1 · 复习（三层混合，取较弱方向）
    cand = {
        "words": [w.lemma for w in ep.words],
        "chunks": [c.id for c in ep.chunks],
        "sentences": [s.id for s in ep.sentences],
    }
    exclude = {
        "words": set(words) | set(spec.bonus_words),
        "chunks": set(chunks) | set(spec.bonus_chunks),
        "sentences": set(sents) | set(spec.bonus_sentences),
    }
    per = max(1, MAX_REVIEW // 3)
    review: dict[str, list[str]] = {}
    for dom in ("words", "chunks", "sentences"):
        if review_in is not None:
            # 续上：用当初挑好的，保证卡序一致
            review[dom] = list(review_in.get(dom, []))
        else:
            review[dom] = progress.pick_review(
                cand[dom], limit=per, domain=dom, exclude=exclude[dom]
            )
    # 方向也要一次定死并落盘：weaker_direction 读 streak，上课中 streak 在变，
    # 续上时重算会翻转方向 → 卡序变化。
    dirs: dict[str, str] = dict(dirs_in or {})

    def direction_for(dom: str, item_id: str) -> str:
        key = f"{dom}:{item_id}"
        if key not in dirs:
            dirs[key] = progress.weaker_direction(dom, item_id)
        return dirs[key]

    for lemma in review["words"]:
        cards.append(word_card(1, direction_for("words", lemma), lemma))
    for cid in review["chunks"]:
        d = direction_for("chunks", cid)
        cards.append(chunk_card(1, "i2a" if d == "i2a" else "chunk", cid, review["chunks"]))
    for sid in review["sentences"]:
        d = direction_for("sentences", sid)
        cards.append(sent_card(1, "i2a" if d == "i2a" else "sentence", sid, review["sentences"]))

    # 2 · 抽检（已勾会的，各 1 题）
    spot: dict[str, list[str]] = {
        "words": [w for w in (known.get("words") or [])[:MAX_SPOT_CHECK]
                  if w in {x.lemma for x in ep.words}],
        "chunks": [c for c in (known.get("chunks") or [])[:MAX_SPOT_CHECK]
                   if c in {x.id for x in ep.chunks}],
    }
    for lemma in spot["words"]:
        cards.append(word_card(2, "a2i", lemma))
    for cid in spot["chunks"]:
        cards.append(chunk_card(2, "chunk", cid, [c.id for c in ep.chunks]))

    # 3/4/5 · 生词
    for lemma in words:
        c = word_card(3, "a2i", lemma)
        cards.append(c)
        proto[f"words:{lemma}"] = c
    for lemma in spec.bonus_words:
        if lemma in {w.lemma for w in ep.words}:
            cards.append(word_card(3, "a2i", lemma, bonus=True))
    for lemma in words:
        cards.append(word_card(4, "i2a", lemma))
    for lemma in words:
        cards.append(word_card(5, "shadow", lemma))

    # 6/7/8 · 短语（一等教学点）
    for cid in chunks:
        c = chunk_card(6, "chunk", cid, chunks)
        cards.append(c)
        proto[f"chunks:{cid}"] = c
    for cid in spec.bonus_chunks:
        if cid in {x.id for x in ep.chunks}:
            cards.append(chunk_card(6, "chunk", cid, chunks, bonus=True))
    for cid in chunks:
        cards.append(chunk_card(7, "i2a", cid, chunks))
    for cid in chunks:
        cards.append(chunk_card(8, "shadow", cid, chunks))

    # 9 · 中场（降载）
    if sents:
        s = ep.sentence(sents[0])
        cards.append(Card(
            card_id="s9:interlude", segment_index=9, kind="passive",
            domain="sentences", item_id=s.id, image=s.image,
            audio_clips=(s.audio_clip,), needs_answer=False,
        ))

    # 10/11/12 · 句子（一等教学点）
    for sid in sents:
        c = sent_card(10, "sentence", sid, sents)
        cards.append(c)
        proto[f"sentences:{sid}"] = c
    for sid in spec.bonus_sentences:
        if sid in {x.id for x in ep.sentences}:
            cards.append(sent_card(10, "sentence", sid, sents, bonus=True))
    for sid in sents:
        cards.append(sent_card(11, "i2a", sid, sents))
    for sid in sents:
        cards.append(sent_card(12, "shadow", sid, sents))

    # 13 · 混打（本节 + 复习，取较弱方向）
    for lemma in words + review["words"]:
        cards.append(word_card(13, direction_for("words", lemma), lemma))
    for cid in chunks + review["chunks"]:
        d = direction_for("chunks", cid)
        cards.append(chunk_card(13, "i2a" if d == "i2a" else "chunk", cid, chunks or review["chunks"]))

    # 14 · 重做占位
    cards.append(Card(card_id="s14:placeholder", segment_index=14, kind="passive",
                      domain="words", item_id="", needs_answer=False))

    # 15 · 盲听
    if sents:
        cards.append(Card(
            card_id="s15:blind", segment_index=15, kind="assess",
            domain="sentences", item_id=",".join(sents),
            audio_clips=tuple(ep.sentence(s).audio_clip for s in sents),
            needs_answer=False,
        ))

    # 16 · 报告
    cards.append(Card(card_id="s16:report", segment_index=16, kind="report",
                      domain="words", item_id="", needs_answer=False))
    return cards, proto, {"review": review, "spot": spot, "dirs": dirs}


__all__ = ["SEGMENTS3", "SEG3_BY_INDEX", "LessonRuntime3", "Segment3", "Seg3"]
