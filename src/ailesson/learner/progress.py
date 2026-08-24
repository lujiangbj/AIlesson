"""掌握度与复习调度（FR-5）。

沿用 illit-english-mvp 的模型：三个域（words/chunks/sentences）× 双方向 streak。
双方向是关键 —— 只会「听音选图」不算学会，还得能「看图选音」，后者逼的是
语义→声音的检索方向。

state 结构与 MVP 兼容（NFR-4），能直接吃下旧进度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

MASTERY_STREAK = 2

Domain = Literal["words", "chunks", "sentences"]
Direction = Literal["a2i", "i2a"]
Status = Literal["unseen", "learning", "mastered"]
DOMAINS: tuple[Domain, ...] = ("words", "chunks", "sentences")


@dataclass
class Entry:
    streak_a2i: int = 0
    streak_i2a: int = 0
    seen: int = 0
    correct: int = 0
    wrong: int = 0
    last_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        # 键名沿用 MVP（lastAt 而非 last_at），保持 state 文件互通
        return {
            "streak_a2i": self.streak_a2i,
            "streak_i2a": self.streak_i2a,
            "seen": self.seen,
            "correct": self.correct,
            "wrong": self.wrong,
            "lastAt": self.last_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Entry:
        # 更老的结构只有单个 streak，迁移到 a2i
        a2i = d.get("streak_a2i")
        if a2i is None:
            a2i = d.get("streak", 0)
        return cls(
            streak_a2i=int(a2i or 0),
            streak_i2a=int(d.get("streak_i2a", 0) or 0),
            seen=int(d.get("seen", 0) or 0),
            correct=int(d.get("correct", 0) or 0),
            wrong=int(d.get("wrong", 0) or 0),
            last_at=int(d.get("lastAt", d.get("last_at", 0)) or 0),
        )


@dataclass
class Progress:
    data: dict[str, dict[str, Entry]] = field(
        default_factory=lambda: {d: {} for d in DOMAINS}
    )

    def entry(self, domain: Domain, item_id: str) -> Entry:
        return self.data.setdefault(domain, {}).setdefault(item_id, Entry())

    def record(
        self, domain: Domain, item_id: str, direction: Direction, correct: bool, at: int = 0
    ) -> Entry:
        e = self.entry(domain, item_id)
        e.seen += 1
        if correct:
            e.correct += 1
        else:
            e.wrong += 1
        # 答错清零：连对才算数
        if direction == "a2i":
            e.streak_a2i = e.streak_a2i + 1 if correct else 0
        else:
            e.streak_i2a = e.streak_i2a + 1 if correct else 0
        if at:
            e.last_at = at
        return e

    def weaker_direction(self, domain: Domain, item_id: str) -> Direction:
        """哪个方向更弱 —— 复习该补的就是这一向。

        掌握要求双向都达标，只练强的那向等于白练。平手时先练 a2i（听音选图更简单，
        适合开场热身）。
        """
        e = self.entry(domain, item_id)
        return "i2a" if e.streak_i2a < e.streak_a2i else "a2i"

    def is_mastered(self, domain: Domain, item_id: str) -> bool:
        e = self.entry(domain, item_id)
        return e.streak_a2i >= MASTERY_STREAK and e.streak_i2a >= MASTERY_STREAK

    def status(self, domain: Domain, item_id: str) -> Status:
        e = self.entry(domain, item_id)
        if not e.seen:
            return "unseen"
        return "mastered" if self.is_mastered(domain, item_id) else "learning"

    def pick_review(
        self,
        candidates: Iterable[str],
        limit: int = 8,
        domain: Domain = "words",
        exclude: set[str] | None = None,
    ) -> list[str]:
        """挑复习词：学过的里面，错得多的、久未碰的先来。

        未学过的不进复习池 —— 那是新词，归首触环节管。
        """
        exclude = exclude or set()
        pool = []
        for cid in candidates:
            if cid in exclude:
                continue
            e = self.entry(domain, cid)
            if not e.seen:
                continue
            # 排序：错过的最优先，然后是「差一点就掌握」的。
            #
            # gap 是关键：正课词双向各练过一次(1,1)，gap=2；顺带词只出过 1 题(1,0)，
            # gap=3。正课词离掌握更近，复习它们能真正推进掌握 —— 而顺带词本来就是
            # 轻过的，抢了复习位反而让正课词永远升不到掌握。
            gap = max(0, MASTERY_STREAK - e.streak_a2i) + max(
                0, MASTERY_STREAK - e.streak_i2a
            )
            pool.append((-e.wrong, gap, e.last_at, cid))
        pool.sort()
        return [cid for *_, cid in pool[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "progress": {
                d: {k: v.to_dict() for k, v in self.data.get(d, {}).items()}
                for d in DOMAINS
            }
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Progress:
        raw = d.get("progress", d) or {}
        out: dict[str, dict[str, Entry]] = {dom: {} for dom in DOMAINS}
        for dom in DOMAINS:
            for k, v in (raw.get(dom) or {}).items():
                out[dom][k] = Entry.from_dict(v)
        # 最老的结构把词直接挂在 progress 根下
        for k, v in raw.items():
            if k in DOMAINS or not isinstance(v, dict):
                continue
            if any(f in v for f in ("streak", "streak_a2i", "seen")):
                out["words"][k] = Entry.from_dict(v)
        return cls(data=out)
