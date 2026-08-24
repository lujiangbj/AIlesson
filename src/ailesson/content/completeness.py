"""内容完备度：把「教具需要什么素材」和「这一集有什么素材」对起来。

教具表声明 `needs`（contract/tools.py），素材层提供字段（contract/episode.py），
这里做交叉，产出教研后台的矩阵：**哪个教学点能跑哪些教具、缺的是什么**。

为什么值得单独一层：这个判断原先只存在于 content/friends_lesson.py 的一句注释
——「只收图和音都齐的词」。后果是缺素材要等上课时才发现（环节 3/4 空转），
而 Friends 线卡住的真正原因（逐字稿没时间轴 → 没 audio_clip → 句子原声跑不了）
在代码里根本看不见。

只依赖契约层：教研不该知道学习者的存在，也不该依赖教室端的编排。
调用方想按编排过滤，自己传 tool_ids。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ailesson.contract.episode import Episode
from ailesson.contract.tools import DOMAINS, TOOLS, Tool, missing_assets

# 素材缺失的中文说法，给后台直接显示
ASSET_LABEL = {
    "audio": "音频",
    "audio_slow": "慢速音频",
    "audio_clip": "原片切片",
    "image": "配图",
    "meaning_zh": "中文释义",
    "text": "文本",
}


def assets_of(ep: Episode, domain: str, item_id: str) -> set[str]:
    """这个教学点实际拥有哪些素材。空字符串算没有。"""
    have: set[str] = set()
    if domain == "words":
        w = ep.word(item_id)
        pairs = [("audio", w.audio), ("audio_slow", w.audio_slow),
                 ("meaning_zh", w.meaning_zh), ("text", item_id)]
        # skip_image 是「这个词有意不配图」（抽象词画不出来），不是漏了
        if not w.skip_image:
            pairs.append(("image", w.image))
    elif domain == "chunks":
        c = ep.chunk(item_id)
        pairs = [("audio", c.audio_tts), ("audio_slow", c.audio_tts_slow),
                 ("image", c.image), ("meaning_zh", c.meaning_zh),
                 ("text", c.text)]
    elif domain == "sentences":
        s = ep.sentence(item_id)
        # audio_clip 指向 TTS 的不算原片切片。Friends 的转换器就是这么填的
        # （audio_clip == audio_tts），字段非空但内容是合成音 —— 而 FR-4.5
        # 要的是原片，「听懂真实语流」是产品核心。只查非空会漏掉这个缺口。
        real_clip = s.audio_clip if s.audio_clip != s.audio_tts else ""
        pairs = [("audio", s.audio_clip or s.audio_tts),
                 ("audio_slow", s.audio_tts_slow),
                 ("audio_clip", real_clip),
                 ("image", s.image), ("meaning_zh", s.meaning_zh),
                 ("text", s.text)]
    else:
        raise KeyError(f"未知的域 {domain!r}；可用：{list(DOMAINS)}")

    for name, val in pairs:
        if val:
            have.add(name)
    return have


def label_of(ep: Episode, domain: str, item_id: str) -> str:
    """人看得懂的内容。矩阵里不能只列 s07 / guess_what。"""
    try:
        if domain == "words":
            return item_id
        if domain == "chunks":
            return ep.chunk(item_id).text
        return ep.sentence(item_id).text
    except KeyError:
        return item_id


@dataclass
class ItemStatus:
    """一个教学点的完备度。"""

    domain: str
    item_id: str
    label: str
    have: set[str] = field(default_factory=set)
    runnable: list[str] = field(default_factory=list)          # 教具 id
    blocked: dict[str, tuple[str, ...]] = field(default_factory=dict)  # id → 缺什么
    skip_image: bool = False

    @property
    def ready(self) -> bool:
        """所有适用教具都能跑。"""
        return not self.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "item_id": self.item_id,
            "label": self.label,
            "have": sorted(self.have),
            "runnable": list(self.runnable),
            "blocked": {k: list(v) for k, v in self.blocked.items()},
            "blocked_zh": {
                k: "、".join(ASSET_LABEL.get(a, a) for a in v)
                for k, v in self.blocked.items()
            },
            "ready": self.ready,
            "skip_image": self.skip_image,
        }


def _tools_for(domain: str, tool_ids: list[str] | None) -> list[Tool]:
    picked = [TOOLS[t] for t in tool_ids if t in TOOLS] if tool_ids \
        else list(TOOLS.values())
    return [t for t in picked if domain in t.domains]


def item_status(
    ep: Episode, domain: str, item_id: str, tool_ids: list[str] | None = None
) -> ItemStatus:
    have = assets_of(ep, domain, item_id)
    skip = bool(domain == "words" and ep.word(item_id).skip_image)
    st = ItemStatus(domain=domain, item_id=item_id,
                    label=label_of(ep, domain, item_id),
                    have=have, skip_image=skip)
    for t in _tools_for(domain, tool_ids):
        miss = missing_assets(t, have)
        if miss:
            st.blocked[t.id] = miss
        else:
            st.runnable.append(t.id)
    return st


def _ids(ep: Episode, domain: str) -> list[str]:
    if domain == "words":
        return [w.lemma for w in ep.words]
    if domain == "chunks":
        return [c.id for c in ep.chunks]
    return [s.id for s in ep.sentences]


def audit(ep: Episode, tool_ids: list[str] | None = None) -> dict[str, Any]:
    """整集的完备度矩阵。

    `blockers` 是最有用的一栏：按「缺得最多」排序，直接告诉教研下一步该去
    生产什么。例如 Friends 会显示「原片切片 缺 71 条句子」。
    """
    used: list[str] = []
    domains: dict[str, Any] = {}
    blocker_count: dict[str, dict[str, Any]] = {}

    for dom in DOMAINS:
        tools = _tools_for(dom, tool_ids)
        for t in tools:
            if t.id not in used:
                used.append(t.id)
        by_tool = {t.id: {"ok": 0, "missing": 0} for t in tools}
        items = []
        ready = 0
        for item_id in _ids(ep, dom):
            st = item_status(ep, dom, item_id, tool_ids)
            items.append(st.to_dict())
            if st.ready:
                ready += 1
            for t in tools:
                key = "missing" if t.id in st.blocked else "ok"
                by_tool[t.id][key] += 1
            for miss in st.blocked.values():
                for a in miss:
                    b = blocker_count.setdefault(
                        a, {"asset": a, "label": ASSET_LABEL.get(a, a),
                            "count": 0, "domains": []})
                    b["count"] += 1
                    if dom not in b["domains"]:
                        b["domains"].append(dom)
        domains[dom] = {
            "total": len(items),
            "ready": ready,
            "items": items,
            "by_tool": by_tool,
        }

    return {
        "episode_id": ep.id,
        "title": ep.title,
        "tools": [TOOLS[t].to_dict() for t in used],
        "domains": domains,
        "blockers": sorted(blocker_count.values(),
                           key=lambda b: -b["count"]),
    }


__all__ = ["ASSET_LABEL", "ItemStatus", "assets_of", "audit", "item_status",
           "label_of"]
