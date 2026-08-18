"""自评结果：词 / 短语 / 句子三层各自分池。

为什么三层都要分池：CET-6 用户实测勾掉 48/53 个词，只剩 1 节课教 5 个生词 ——
但他真正不会的是 `it's only X`、`oh goodness me`、`look at the mess you're in`
这些短语和句子。**「认识这个词」和「能张口说出这句」是两个维度**，只在词层分池
会把 90% 该学的内容筛掉。

替代了原来的顺序分诊（一题一题问 + 连续 5 个不会提前终止）：铺开勾选更快，
而且能横向比较，判断更准。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

Domain = Literal["words", "chunks", "sentences"]
DOMAINS: tuple[Domain, ...] = ("words", "chunks", "sentences")
HowKnown = Literal["self", "demoted"]


@dataclass
class SelfAssessment:
    """勾选结果。每层一个「会 / 不会」二分。"""

    episode_id: str
    known: dict[str, list[str]] = field(
        default_factory=lambda: {d: [] for d in DOMAINS}
    )
    unknown: dict[str, list[str]] = field(
        default_factory=lambda: {d: [] for d in DOMAINS}
    )
    how: dict[str, dict[str, HowKnown]] = field(
        default_factory=lambda: {d: {} for d in DOMAINS}
    )
    at: int = 0

    # ---- 便捷访问 ----

    @property
    def known_words(self) -> list[str]:
        return self.known["words"]

    @property
    def unknown_words(self) -> list[str]:
        return self.unknown["words"]

    @property
    def known_chunks(self) -> list[str]:
        return self.known["chunks"]

    @property
    def unknown_chunks(self) -> list[str]:
        return self.unknown["chunks"]

    @property
    def known_sentences(self) -> list[str]:
        return self.known["sentences"]

    @property
    def unknown_sentences(self) -> list[str]:
        return self.unknown["sentences"]

    def total_unknown(self) -> int:
        """教学点总数 —— 决定要上几节课。"""
        return sum(len(v) for v in self.unknown.values())

    def demote(self, domain: Domain, item_id: str) -> None:
        """抽检答错 → 从会池打回不会池（FR-2.2）。"""
        if item_id not in self.known.get(domain, []):
            return
        self.known[domain].remove(item_id)
        if item_id not in self.unknown[domain]:
            self.unknown[domain].append(item_id)
        self.how[domain][item_id] = "demoted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "known": self.known,
            "unknown": self.unknown,
            "how": self.how,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SelfAssessment:
        def _fill(key: str) -> dict[str, list[str]]:
            raw = d.get(key) or {}
            return {dom: list(raw.get(dom, [])) for dom in DOMAINS}

        raw_how = d.get("how") or {}
        return cls(
            episode_id=d["episode_id"],
            known=_fill("known"),
            unknown=_fill("unknown"),
            how={dom: dict(raw_how.get(dom, {})) for dom in DOMAINS},
            at=d.get("at", 0),
        )


def build_assessment(
    episode_id: str,
    all_items: dict[str, list[str]],
    known_items: dict[str, list[str]],
) -> SelfAssessment:
    """勾选 → 自评结果。勾中的进会池，其余进不会池。

    勾选是全览式的：每个条目都过了一遍眼，没有「未问到所以推定不会」这回事。
    """
    a = SelfAssessment(episode_id=episode_id, at=int(time.time()))
    for dom in DOMAINS:
        valid = list(all_items.get(dom, []))
        picked = {x for x in known_items.get(dom, []) if x in set(valid)}
        a.known[dom] = [x for x in valid if x in picked]
        a.unknown[dom] = [x for x in valid if x not in picked]
        a.how[dom] = {x: "self" for x in valid}
    return a
