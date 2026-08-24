"""状态汇总：前端每次操作后都读它。

有意做成一个宽接口而不是十个窄接口 —— 前端是整页重渲染（原生 JS 无框架），
一次拿全比拼十个请求简单，也不会出现「用户已切、课表还是旧的」这种半新半旧。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ailesson.server.deps import S

router = APIRouter(tags=["status"])


@router.get("/api/status")
def status() -> dict[str, Any]:
    s = S()
    sess = s.session
    a = sess.assessment
    cur = s.users.current()
    out: dict[str, Any] = {
        "user": cur.to_dict() if cur else None,
        "episode": {
            "id": sess.episode.id,
            "title": sess.episode.title,
            "words": len(sess.episode.words),
            "chunks": len(sess.episode.chunks),
            "sentences": len(sess.episode.sentences),
        },
        "max_lessons_hint": sess.max_lessons_hint(),
        "assessed": a is not None,
        "completed_lessons": sess.completed_lessons,
        "in_lesson": s.runtime is not None and not s.runtime.finished,
        "arrangement": {"id": s.arrangement.id, "version": s.arrangement.version,
                        "title": s.arrangement.title},
    }
    if a:
        out["assessment"] = {
            "known": {k: len(v) for k, v in a.known.items()},
            "unknown": {k: len(v) for k, v in a.unknown.items()},
            "total_unknown": a.total_unknown(),
        }
    if sess.selection:
        out["selection"] = {
            "source": sess.selection.get("source"),
            "chunks": len(sess.selection.get("chunks") or []),
            "sentences": len(sess.selection.get("sentences") or []),
        }
    if sess.probe and sess.probe.get("calibration"):
        out["probe"] = sess.probe["calibration"]
    if sess.plan:
        # fallback=True 表示 LLM 分组失败、用了机械划分。这个信号必须暴露到
        # 前端，否则用户拿到「第1组 / 补充N」的烂课表却不知道原因
        out["plan"] = {"fallback": sess.plan.fallback,
                       "n_lessons": len(sess.plan.lessons)}
        out["lessons"] = [
            {
                "index": l.index,
                "theme": l.theme,
                "words": l.focus_words,
                "chunks": [sess.episode.chunk(c).text for c in l.chunk_ids],
                "sentences": [sess.episode.sentence(x).text
                              for x in l.sentence_ids],
                "n_points": l.n_points,
                "done": l.index in sess.completed_lessons,
            }
            for l in sess.plan.lessons
        ]
    if s.runtime:
        out["lesson_progress"] = {
            "index": s.runtime.lesson_index,
            "cursor": s.runtime.cursor,
            "total": len(s.runtime.cards),
            "paused": False,
        }
    else:
        # 退出后 runtime 没了，但盘上快照还在 —— 课程表要显示「点这里继续」
        info = s.paused_info()
        if info and info.get("index") is not None:
            out["lesson_progress"] = {**info, "paused": True}
    if s.stale_snapshot:
        # 编排换了，旧快照不能重建。前端要提示「这节得重开」，
        # 而不是假装能续上然后错位到别的卡
        out["stale_snapshot"] = s.stale_snapshot
    return out
