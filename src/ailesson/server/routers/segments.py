"""剧本与切段：看拆分效果。

**只读**。第一步（逐字稿来源 + 切段规则）还没定型，规则由人给、走代码改；
后台这里的职责是把「切成了什么样」摊开，让人能判断规则对不对。

规则定型之后这里会加写操作（重切、调段数、手动挪切点）。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from ailesson.content.segment import (
    RULE,
    WORDS_PER_LESSON,
    SegmentPlan,
    load_plan,
    split_chunks,
)
from ailesson.server.state import ROOT

router = APIRouter(prefix="/api/content", tags=["content"])

PARSED = ROOT / "data" / "friends" / "parsed"
VOCAB = ROOT / "data" / "friends" / "vocab"
SEGMENTS = ROOT / "data" / "friends" / "segments"
LESSONS = ROOT / "data" / "friends" / "lessons"


def _parsed_ids() -> list[str]:
    if not PARSED.is_dir():
        return []
    return sorted(p.stem for p in PARSED.glob("*.json"))


def _read_doc(ep_id: str) -> dict[str, Any]:
    p = PARSED / f"{ep_id}.json"
    if not p.exists():
        raise HTTPException(404, f"没有解析好的剧本 {ep_id}，先跑 friends_parse.py")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"剧本 {ep_id} 解析不了：{e}") from e


def _levels(ep_id: str) -> dict[str, str] | None:
    p = VOCAB / f"{ep_id}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    return {
        e["token"]: e["level"]
        for e in data.get("entries", [])
        if e.get("level") and e.get("category") in ("word", "contraction")
    }


@router.get("/scripts")
def scripts() -> dict[str, Any]:
    """有哪些剧本，各自到了哪一步。

    每集的状态是「算出来的」不是记下来的 —— 看产物在不在。流水线状态表要等
    第二步（走后台跑生产）才有意义。
    """
    out = []
    for ep_id in _parsed_ids():
        doc = _read_doc(ep_id)
        st = doc.get("stats", {})
        plan = load_plan(ep_id, SEGMENTS)
        out.append({
            "episode_id": ep_id,
            "title": doc.get("title", ""),
            "season": doc.get("season"),
            "episode": doc.get("episode"),
            "lines": st.get("lines", 0),
            "scenes": st.get("scenes", 0),
            "speakers": len(st.get("speakers", [])),
            "has_vocab": (VOCAB / f"{ep_id}.json").exists(),
            "has_segments": plan is not None,
            "n_segments": plan.n if plan else None,
            "spread": plan.spread if plan else None,
            "has_lesson": (LESSONS / f"lesson-friends-{ep_id}.json").exists(),
        })
    return {"scripts": out, "rule": RULE}


@router.get("/scripts/{ep_id}/chunks")
def chunks(ep_id: str) -> dict[str, Any]:
    """换场切出来的 chunk —— 切段的**最小单位**。

    这是判断「能不能切得更均」的关键：任何一段至少包含一个完整 chunk，
    所以最大的 chunk 就是段大小的下限。Friends S1E1 的开场 Central Perk
    是 1469 词、占全集 39%，切不开 —— 段数越多反而越不均。
    """
    doc = _read_doc(ep_id)
    cs = split_chunks(doc["items"])
    total = sum(c.words for c in cs) or 1
    rows = [
        {
            "index": i,
            "location": c.location,
            "scene": c.scene or "",
            "lines": len(c.lines),
            "words": c.words,
            "share": round(c.words / total * 100, 1),
        }
        for i, c in enumerate(cs, 1)
    ]
    biggest = max(rows, key=lambda r: r["words"]) if rows else None
    return {
        "episode_id": ep_id,
        "title": doc.get("title", ""),
        "n_chunks": len(rows),
        "total_words": total,
        "chunks": rows,
        # 段大小的下限：最大 chunk 切不开
        "floor_words": biggest["words"] if biggest else 0,
        "floor_at": biggest["index"] if biggest else None,
    }


@router.get("/scripts/{ep_id}/segments")
def segments(ep_id: str, n: int | None = None) -> dict[str, Any]:
    """切段效果。

    n 不给就读落盘的计划；给了就现算一份预览（不落盘）——
    用来对比「切 4 段还是 5 段」。
    """
    doc = _read_doc(ep_id)
    if n is None:
        plan = load_plan(ep_id, SEGMENTS)
        if plan is None:
            plan = SegmentPlan.build(
                ep_id, doc["items"], levels=_levels(ep_id),
                title=doc.get("title", ""))
            saved = False
        else:
            saved = True
    else:
        plan = SegmentPlan.build(
            ep_id, doc["items"], n=n, levels=_levels(ep_id),
            title=doc.get("title", ""))
        saved = False

    return {**plan.to_dict(), "saved": saved,
            "words_per_lesson": WORDS_PER_LESSON}


@router.get("/scripts/{ep_id}/compare")
def compare(ep_id: str, lo: int = 3, hi: int = 7) -> dict[str, Any]:
    """并排比几种段数，看哪个切得齐。

    「切 4 段还是 5 段」这个决定需要横着看，一份一份点开比不出来。
    """
    doc = _read_doc(ep_id)
    lo, hi = max(1, lo), min(hi, 12)
    out = []
    for k in range(lo, hi + 1):
        p = SegmentPlan.build(ep_id, doc["items"], n=k)
        if p.n != k:          # 换场数不够，切不出这么多段
            continue
        out.append({
            "n": p.n,
            "spread": p.spread,
            "words": [s["words"] for s in p.segments],
            "lines": [s["lines"] for s in p.segments],
        })
    best = min(out, key=lambda x: x["spread"])["n"] if out else None
    return {"episode_id": ep_id, "options": out, "most_even": best}


@router.get("/scripts/{ep_id}/segments/{index}/lines")
def segment_lines(ep_id: str, index: int) -> dict[str, Any]:
    """某一段的全部台词。核对切点切得对不对，最终得看原文。"""
    doc = _read_doc(ep_id)
    plan = load_plan(ep_id, SEGMENTS) or SegmentPlan.build(
        ep_id, doc["items"], levels=_levels(ep_id))
    if not 1 <= index <= plan.n:
        raise HTTPException(404, f"{ep_id} 没有第 {index} 段")

    # 按落盘的段边界重放一遍 chunk，取这一段的台词
    cs = split_chunks(doc["items"])
    counts = [s.get("n_chunks") or len(s["scenes"]) or 1
              for s in plan.segments]
    pos = sum(counts[: index - 1])
    picked = cs[pos: pos + counts[index - 1]]

    out = []
    for c in picked:
        out.append({"type": "scene", "text": c.scene or "开场"})
        for ln in c.lines:
            out.append({
                "type": "line",
                "speaker": ln.get("speaker") or "",
                "text": ln.get("text") or "",
            })
    seg = plan.segments[index - 1]
    return {
        "episode_id": ep_id, "index": index,
        "words": seg["words"], "lines": seg["lines"],
        "locations": seg["locations"],
        "items": out,
    }
