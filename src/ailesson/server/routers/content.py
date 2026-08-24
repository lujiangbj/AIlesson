"""教研内容后台：素材目录 + 完备度矩阵。

只读。内容生产还在 scripts/ 里跑（切段 / 分级 / 配图 / TTS），这里先把
「产出的东西够不够教」变成看得见的一张表。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ailesson.content.completeness import audit
from ailesson.server.deps import S
from ailesson.server.routers.status import status

router = APIRouter(prefix="/api/content", tags=["content"])


@router.get("/episodes")
def episodes() -> dict[str, Any]:
    """可选素材列表。只列 lesson JSON 真的存在的。"""
    s = S()
    return {
        "episodes": [{**e, "current": e["id"] == s.episode_id}
                     for e in s.repo.available()],
        "current": s.episode_id,
    }


@router.post("/episodes/{episode_id}/select")
def select_episode(episode_id: str) -> dict[str, Any]:
    """换素材。会重置当前会话 —— 自评和课程划分都是按集算的。"""
    s = S()
    try:
        s.select_episode(episode_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return status()


@router.get("/completeness")
def completeness(
    episode_id: str | None = None,
    arrangement_only: bool = Query(
        True, description="只看当前编排真的用到的教具"),
) -> dict[str, Any]:
    """完备度矩阵：每个教学点能跑哪些教具、缺什么素材。

    默认只审当前编排用到的教具 —— 报「短语缺配图」没意义，编排里短语走的是
    听音选义，本来就不用图。
    """
    s = S()
    eid = episode_id or s.episode_id
    try:
        ep = s.episode if eid == s.episode_id else s.repo.load(eid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e

    tool_ids = None
    if arrangement_only:
        tool_ids = sorted({t for st in s.arrangement.steps
                           for t in st.tool_ids()})
    return {**audit(ep, tool_ids=tool_ids),
            "arrangement_only": arrangement_only,
            "arrangement": s.arrangement.id}
