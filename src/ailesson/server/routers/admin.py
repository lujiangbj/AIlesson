"""后台：教具表、编排、课程计划检查器。

计划检查器是**只读**的，而且必须同时给出输入和输出。只看输出（46 张卡的清单）
对迭代没用 —— 一节课有 12 个点，你会知道「多了」，但不知道是自评的锅、探测
阈值的锅，还是 LLM 聚类的锅。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ailesson.classroom.runtime import LessonRuntime
from ailesson.contract.tools import TOOLS
from ailesson.server.deps import S, need_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tools")
def tools() -> dict[str, Any]:
    """教具表。一节课是由这些拼起来的。"""
    return {"tools": [t.to_dict() for t in TOOLS.values()]}


@router.get("/arrangement")
def arrangement() -> dict[str, Any]:
    """当前编排：16 环节各用什么教具、内容从哪来、计不计分。"""
    return S().arrangement.to_dict()


@router.get("/plan")
def plan() -> dict[str, Any]:
    """课程计划检查器：AI 组出来的课到底是什么。

    plan 是 `pack_course(SelfAssessment)` 的函数值，要判断组得对不对，必须
    并排看到输入（勾了什么、探测推出什么）、LLM 那一步（是否走了兜底）、
    输出（N 节课怎么分）。
    """
    s = need_user()
    sess = s.session
    a = sess.assessment
    out: dict[str, Any] = {
        "user": s.uid,
        "episode": {"id": sess.episode.id, "title": sess.episode.title,
                    "words": len(sess.episode.words),
                    "chunks": len(sess.episode.chunks),
                    "sentences": len(sess.episode.sentences)},
        "has_plan": sess.plan is not None,
    }

    # ---- 输入 1：自评 ----
    if a:
        out["assessment"] = {
            "at": a.at,
            "known": {k: list(v) for k, v in a.known.items()},
            "unknown": {k: list(v) for k, v in a.unknown.items()},
            "counts": {k: {"known": len(a.known[k]), "unknown": len(a.unknown[k])}
                       for k in a.known},
            "total_unknown": a.total_unknown(),
            # demoted：抽检答错被打回待学池的，说明自评不可信
            "how": {k: dict(v) for k, v in a.how.items()},
        }

    # ---- 输入 2：探测 + 动态挑选 ----
    if sess.probe:
        out["probe"] = {
            "asked": sess.probe.get("asked", []),
            "answers": sess.probe.get("answers", {}),
            "calibration": sess.probe.get("calibration"),
        }
    if sess.selection:
        out["selection"] = sess.selection

    # ---- 输出：N 节课 ----
    if sess.plan:
        out["plan"] = {
            "at": sess.plan.at,
            "fallback": sess.plan.fallback,
            "fallback_hint": ("LLM 分组失败，这是机械划分的结果"
                              if sess.plan.fallback else ""),
            "n_lessons": len(sess.plan.lessons),
            "lessons": [
                {
                    **l.to_dict(),
                    "n_points": l.n_points,
                    "done": l.index in sess.completed_lessons,
                    # 不露内部 id（§4）
                    "labels": {
                        dom: [sess.label_of(dom, i) for i in ids]
                        for dom, ids in l.items().items()
                    },
                    "bonus_labels": {
                        dom: [sess.label_of(dom, i) for i in ids]
                        for dom, ids in l.bonus_items().items()
                    },
                }
                for l in sess.plan.lessons
            ],
        }
    return out


@router.get("/plan/{index}/cards")
def plan_cards(index: int) -> dict[str, Any]:
    """把某一节展开成卡序，按环节分组。

    不落盘、不影响当前课堂 —— 单独 build 一个 runtime 只为看牌。
    """
    s = need_user()
    spec = s.session.spec_for(index)
    if spec is None:
        raise HTTPException(404, f"没有第 {index} 节课")

    rt = LessonRuntime.build(
        s.episode, spec, s.session.progress,
        known=s.session._spot_check(spec), arrangement=s.arrangement,
    )
    # 重做卡要等走到那一环才建，检查器直接把当前错题清单展开一次
    rt._redo_built = True
    rt._insert_redo()

    groups: list[dict[str, Any]] = []
    for step in s.arrangement.steps:
        cards = [c for c in rt.cards if c.step_index == step.index]
        groups.append({
            "step": step.to_dict(),
            "n_cards": len(cards),
            "cards": [
                {
                    "card_id": c.card_id,
                    "tool": c.tool,
                    "tool_name": c.tool_name,
                    "interaction": c.interaction,
                    "direction": c.direction,
                    "domain": c.domain,
                    "item_id": c.item_id,
                    "label": s.session.label_of(c.domain, c.item_id)
                    if c.item_id else "",
                    "is_bonus": c.is_bonus,
                    "needs_answer": c.needs_answer,
                    "n_choices": len(c.choices),
                    "has_audio": bool(c.prompt_audio or c.audio_clips),
                    "has_image": bool(c.image),
                }
                for c in cards
            ],
        })

    return {
        "index": index,
        "theme": spec.theme,
        "n_points": spec.n_points,
        "total_cards": len(rt.cards),
        "arrangement": {"id": s.arrangement.id,
                        "version": s.arrangement.version},
        "picked": {
            "review": rt.review_picked,
            "spot": rt.spot_picked,
            "directions": rt.dir_picked,
        },
        "groups": groups,
    }
