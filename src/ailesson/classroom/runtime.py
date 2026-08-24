"""课堂运行时：按编排把一节课展开成卡序，并推进。

三层（词 / 短语 / 句子）都是一等教学点。CET-6 用户的一节课可能一个生词都没有，
全是短语和句子 —— 那也必须能正常跑完，且每个点都有完整的曝光形态。

课内仍不产生掌握（每方向只 1 次），沿用诚实原则。

## 编排驱动

环节顺序、每环发什么卡、哪些环节计分，全从 `arrangement.Arrangement` 读。原先
这些是写死的分支加魔数（`STREAK_SEGMENTS`、`in (3, 6, 10)`、`== 14`），现在
运行时只认三样东西：环节的 `source`（内容从哪来）、`domains`（发哪几层）、
`tool`（用哪件教具）。

## 卡序必须确定性可重建（§10.5）

快照不存卡，只存重建输入。`pick_review` 读 last_at/streak、`weaker_direction`
读 streak，这些在上课过程中都在变 —— 所以它们的结果必须在建课时定死并落盘
（`review_picked` / `spot_picked` / `dir_picked`），restore 时照抄，不许重算。

编排本身也是重建输入的一部分，见 `arrangement.compatible()`。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ailesson.classroom.arrangement import DEFAULT, Arrangement, Step
from ailesson.classroom.cards import Card, choices_for_item, choices_for_word
from ailesson.contract.episode import Episode
from ailesson.contract.lesson_spec import LessonSpec
from ailesson.learner.progress import Progress

MAX_REVIEW = 9          # 复习点上限（三层合计）
MAX_SPOT_CHECK = 3


@dataclass
class LessonRuntime:
    episode_id: str
    lesson_index: int
    cards: list[Card]
    progress: Progress
    arrangement: Arrangement = DEFAULT
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
        cls, ep: Episode, spec: LessonSpec, progress: Progress,
        known: dict[str, list[str]] | None = None,
        review: dict[str, list[str]] | None = None,
        dirs: dict[str, str] | None = None,
        arrangement: Arrangement = DEFAULT,
    ) -> LessonRuntime:
        cards, proto, picked = _build_cards(
            ep, spec, progress, known or {}, review, dirs, arrangement
        )
        return cls(
            episode_id=ep.id, lesson_index=spec.index,
            cards=cards, progress=progress, arrangement=arrangement, _proto=proto,
            review_picked=picked["review"], spot_picked=picked["spot"],
            dir_picked=picked["dirs"],
        )

    @classmethod
    def restore(
        cls, ep: Episode, spec: LessonSpec, progress: Progress,
        snap: dict[str, Any], known: dict[str, list[str]] | None = None,
        arrangement: Arrangement = DEFAULT,
    ) -> LessonRuntime:
        # 复习/抽检的选择必须从快照读回，不能重新挑：pick_review 的排序依赖
        # last_at 和 streak，上课过程中它们已经变了，重挑会得到不同的卡序 →
        # 续上时错位到别的卡。
        rt = cls.build(
            ep, spec, progress,
            known=snap.get("spot_picked") or known,
            review=snap.get("review_picked"),
            dirs=snap.get("dir_picked"),
            arrangement=arrangement,
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
            # 编排身份：改了编排的旧快照不许重建（§10.5 的延伸）
            **self.arrangement.stamp(),
        }

    # ---- 推进 ----

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.cards)

    def step_of(self, card: Card) -> Step:
        return self.arrangement.step(card.step_index)

    def _redo_index(self) -> int | None:
        return next((s.index for s in self.arrangement.steps
                     if s.source == "redo"), None)

    def current(self) -> Card | None:
        if self.finished:
            return None
        card = self.cards[self.cursor]
        # 重做环节的卡要等走到这里才能建 —— 错题清单在那之前还在变
        if card.step_index == self._redo_index() and not self._redo_built:
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
        step = self.step_of(card)

        self.stats["asked"] += 1
        self.stats["correct" if correct else "wrong"] += 1

        key = f"{card.domain}:{card.item_id}"
        if step.first_touch and key not in self._answered:
            self._answered[key] = correct
            if correct and not card.is_bonus:
                self.stats["first_try_correct"] += 1

        direction = card.direction if card.direction != "none" else "a2i"
        if step.scored and card.is_quiz:
            self.progress.record(card.domain, card.item_id, direction, correct, at)
        elif not correct:
            # 巩固环节不计正向 streak，但答错仍清零 —— 不计分不等于把错误藏起来
            self.progress.record(card.domain, card.item_id, direction, False, at)

        if not correct:
            pair = (card.domain, card.item_id)
            if pair not in self.wrong_items:
                self.wrong_items.append(pair)
            if step.source == "spot" and pair not in self.demoted:
                self.demoted.append(pair)
        self.advance()

    def self_assess(self, score: int) -> None:
        card = self.current()
        if card is not None and card.interaction == "assess":
            self.blind_listen_score = score
            self.advance()

    def _insert_redo(self) -> None:
        redo_at = self._redo_index()
        if redo_at is None:
            return
        for c in [x for x in self.cards if x.step_index == redo_at]:
            self.cards.remove(c)
        if not self.wrong_items:
            return
        at = next((i for i, c in enumerate(self.cards) if c.step_index > redo_at),
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
            # 重做沿用首触那张卡的教具（编排里标的 INHERIT）
            new.append(
                Card(
                    card_id=f"redo:{k}", step_index=redo_at, tool=proto.tool,
                    domain=proto.domain, item_id=proto.item_id,
                    direction=proto.direction,
                    target_words=proto.target_words,
                    prompt_audio=proto.prompt_audio,
                    prompt_audio_slow=proto.prompt_audio_slow,
                    image=proto.image, meaning_zh=proto.meaning_zh,
                    text=proto.text, choices=proto.choices,
                    correct_id=proto.correct_id,
                )
            )
        self.cards[at:at] = new


# ---------------- 建卡 ----------------


@dataclass
class _Ctx:
    """建卡过程中要传给各个发卡器的东西。"""

    ep: Episode
    spec: LessonSpec
    progress: Progress
    cards: list[Card] = field(default_factory=list)
    proto: dict[str, Card] = field(default_factory=dict)
    focus: dict[str, list[str]] = field(default_factory=dict)
    review: dict[str, list[str]] = field(default_factory=dict)
    spot: dict[str, list[str]] = field(default_factory=dict)
    dirs: dict[str, str] = field(default_factory=dict)

    def direction_for(self, dom: str, item_id: str) -> str:
        """较弱方向。一次定死并落盘 —— 上课中 streak 在变，重算会翻转方向。"""
        key = f"{dom}:{item_id}"
        if key not in self.dirs:
            self.dirs[key] = self.progress.weaker_direction(dom, item_id)
        return self.dirs[key]

    def all_ids(self, dom: str) -> list[str]:
        if dom == "words":
            return [w.lemma for w in self.ep.words]
        if dom == "chunks":
            return [c.id for c in self.ep.chunks]
        return [s.id for s in self.ep.sentences]


def _make_card(
    ctx: _Ctx, step: Step, dom: str, item_id: str, pool: list[str],
    *, bonus: bool = False, direction: str | None = None,
) -> Card:
    """按环节声明的教具造一张答题/跟读卡。"""
    t = step.resolve(dom, direction)
    ep = ctx.ep
    cid = f"s{step.index}:{t.id}:{item_id}"

    if dom == "words":
        w = ep.word(item_id)
        return Card(
            card_id=cid, step_index=step.index, tool=t.id, direction=t.direction,
            domain="words", item_id=item_id, target_words=(item_id,),
            prompt_audio=w.audio, prompt_audio_slow=w.audio_slow,
            image="" if w.skip_image else w.image, meaning_zh=w.meaning_zh,
            text=item_id, choices=choices_for_word(ep, item_id, pool),
            correct_id=item_id, is_bonus=bonus,
        )
    if dom == "chunks":
        c = ep.chunk(item_id)
        return Card(
            card_id=cid, step_index=step.index, tool=t.id, direction=t.direction,
            domain="chunks", item_id=item_id, target_words=tuple(c.covers_words),
            prompt_audio=c.audio_tts, prompt_audio_slow=c.audio_tts_slow,
            image=c.image, meaning_zh=c.meaning_zh, text=c.text,
            choices=choices_for_item(ctx.all_ids("chunks"), item_id,
                                     [x for x in pool if x != item_id]),
            correct_id=item_id, is_bonus=bonus,
        )
    s = ep.sentence(item_id)
    return Card(
        card_id=cid, step_index=step.index, tool=t.id, direction=t.direction,
        domain="sentences", item_id=item_id,
        target_words=tuple(ep.words_covered_by_sentence(item_id)),
        # 句子用原片切片，这是「听懂真实语流」的核心
        prompt_audio=s.audio_clip or s.audio_tts,
        prompt_audio_slow=s.audio_tts_slow,
        image=s.image, meaning_zh=s.meaning_zh, text=s.text,
        choices=choices_for_item(ctx.all_ids("sentences"), item_id,
                                 [x for x in pool if x != item_id]),
        correct_id=item_id, is_bonus=bonus,
    )


# ---- 发卡器：每种 source 一个 ----


def _emit_focus(ctx: _Ctx, step: Step) -> None:
    """本节正课教学点。首触环节额外发顺带点，并登记 proto 供重做用。"""
    bonus_by_dom = ctx.spec.bonus_items()
    for dom in step.domains:
        pool = ctx.focus.get(dom, [])
        for item_id in pool:
            c = _make_card(ctx, step, dom, item_id, pool)
            ctx.cards.append(c)
            if step.first_touch:
                ctx.proto[f"{dom}:{item_id}"] = c
        if not step.first_touch:
            continue
        # 顺带点只在首触环节出 1 题，不跟读、不反向（FR-3.4）
        exists = set(ctx.all_ids(dom))
        for item_id in bonus_by_dom.get(dom, []):
            if item_id in exists:
                ctx.cards.append(
                    _make_card(ctx, step, dom, item_id, pool, bonus=True)
                )


def _emit_review(ctx: _Ctx, step: Step) -> None:
    """跨节复习，取较弱方向。"""
    for dom in step.domains:
        picked = ctx.review.get(dom, [])
        for item_id in picked:
            d = ctx.direction_for(dom, item_id)
            ctx.cards.append(_make_card(ctx, step, dom, item_id, picked,
                                        direction=d))


def _emit_spot(ctx: _Ctx, step: Step) -> None:
    """抽检已勾会的。答错要降级回待学池。"""
    for dom in step.domains:
        for item_id in ctx.spot.get(dom, []):
            ctx.cards.append(
                _make_card(ctx, step, dom, item_id, ctx.all_ids(dom))
            )


def _emit_mixed(ctx: _Ctx, step: Step) -> None:
    """本节 + 复习混打，取较弱方向。巩固性质，不计 streak。"""
    for dom in step.domains:
        items = ctx.focus.get(dom, []) + ctx.review.get(dom, [])
        pool = ctx.focus.get(dom) or ctx.review.get(dom, [])
        for item_id in items:
            d = ctx.direction_for(dom, item_id)
            ctx.cards.append(_make_card(ctx, step, dom, item_id, pool,
                                        direction=d))


def _emit_redo(ctx: _Ctx, step: Step) -> None:
    """占位卡。真正的重做卡等走到这一环才建（错题清单在那之前还在变）。"""
    ctx.cards.append(Card(
        card_id=f"s{step.index}:placeholder", step_index=step.index,
        tool="report", domain="words", item_id="", needs_answer=False,
    ))


def _emit_single(ctx: _Ctx, step: Step) -> None:
    """中场：取本节第一个句子，放画面 + 原声，不答题。"""
    sents = ctx.focus.get("sentences", [])
    if not sents:
        return
    s = ctx.ep.sentence(sents[0])
    t = step.resolve("sentences")
    ctx.cards.append(Card(
        card_id=f"s{step.index}:{t.id}", step_index=step.index, tool=t.id,
        domain="sentences", item_id=s.id, image=s.image,
        audio_clips=(s.audio_clip,), needs_answer=False,
    ))


def _emit_all(ctx: _Ctx, step: Step) -> None:
    """盲听：本节全部句子连着放，学生自评听懂多少。"""
    sents = ctx.focus.get("sentences", [])
    if not sents:
        return
    t = step.resolve("sentences")
    ctx.cards.append(Card(
        card_id=f"s{step.index}:{t.id}", step_index=step.index, tool=t.id,
        domain="sentences", item_id=",".join(sents),
        audio_clips=tuple(ctx.ep.sentence(s).audio_clip for s in sents),
        needs_answer=False,
    ))


def _emit_none(ctx: _Ctx, step: Step) -> None:
    """报告等不需要教学点的环节。"""
    t = step.resolve(step.domains[0] if step.domains else "words")
    ctx.cards.append(Card(
        card_id=f"s{step.index}:{t.id}", step_index=step.index, tool=t.id,
        domain="words", item_id="", needs_answer=False,
    ))


_EMITTERS: dict[str, Callable[[_Ctx, Step], None]] = {
    "focus": _emit_focus,
    "review": _emit_review,
    "spot": _emit_spot,
    "mixed": _emit_mixed,
    "redo": _emit_redo,
    "single": _emit_single,
    "all": _emit_all,
    "none": _emit_none,
}


def _pick_review(
    ctx: _Ctx, domains: tuple[str, ...], given: dict[str, list[str]] | None
) -> dict[str, list[str]]:
    """挑复习条目。

    given 非空表示续上 —— 用当初挑好的，不重挑（§10.5）。
    """
    exclude = {
        dom: set(ctx.focus.get(dom, []))
        | set(ctx.spec.bonus_items().get(dom, []))
        for dom in domains
    }
    per = max(1, MAX_REVIEW // max(1, len(domains)))
    out: dict[str, list[str]] = {}
    for dom in domains:
        if given is not None:
            out[dom] = list(given.get(dom, []))
        else:
            out[dom] = ctx.progress.pick_review(
                ctx.all_ids(dom), limit=per, domain=dom, exclude=exclude[dom]
            )
    return out


def _pick_spot(
    ctx: _Ctx, domains: tuple[str, ...], known: dict[str, list[str]]
) -> dict[str, list[str]]:
    """挑抽检条目：已勾会的里取前几个，且必须在本集素材里存在。"""
    out: dict[str, list[str]] = {}
    for dom in domains:
        exists = set(ctx.all_ids(dom))
        out[dom] = [x for x in (known.get(dom) or [])[:MAX_SPOT_CHECK]
                    if x in exists]
    return out


def _build_cards(
    ep: Episode, spec: LessonSpec, progress: Progress,
    known: dict[str, list[str]],
    review_in: dict[str, list[str]] | None = None,
    dirs_in: dict[str, str] | None = None,
    arr: Arrangement = DEFAULT,
) -> tuple[list[Card], dict[str, Card], dict[str, Any]]:
    """按编排把一节课展开成卡序。

    编排决定顺序和每环用什么教具；「内容从哪来」按 source 分派给发卡器。
    原先这里是 175 行写死的分支，环节序号当魔数散在其中。
    """
    ctx = _Ctx(ep=ep, spec=spec, progress=progress,
               focus=spec.items(), dirs=dict(dirs_in or {}))

    # 复习和抽检的选择要先定死（它们依赖会变的 streak / last_at），
    # 然后才能建卡 —— 也是发卡器之间唯一的共享状态
    review_domains = next((s.domains for s in arr.steps if s.source == "review"), ())
    spot_domains = next((s.domains for s in arr.steps if s.source == "spot"), ())
    ctx.review = _pick_review(ctx, review_domains, review_in)
    ctx.spot = _pick_spot(ctx, spot_domains, known)

    for step in arr.steps:
        emit = _EMITTERS.get(step.source)
        if emit is None:
            raise ValueError(
                f"环节 {step.index}（{step.title}）的内容来源 "
                f"{step.source!r} 没有对应的发卡器"
            )
        emit(ctx, step)

    return ctx.cards, ctx.proto, {
        "review": ctx.review, "spot": ctx.spot, "dirs": ctx.dirs,
    }


__all__ = ["MAX_REVIEW", "MAX_SPOT_CHECK", "LessonRuntime"]
