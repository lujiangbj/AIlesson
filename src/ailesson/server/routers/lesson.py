"""教室端：开课 / 答题 / 推进 / 收课 / 答错讲解。

答题永远本地判定（前端算 `choice === correct_id`），这里只记账。
语音层任何失败都不得阻塞答题（FR-6）。
"""
from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ailesson.classroom.report import LessonReport, render_report_text
from ailesson.classroom.runtime import LessonRuntime
from ailesson.classroom.voice import TutorVoice
from ailesson.server.deps import S, label_of, need_lesson, need_user, url
from ailesson.server.routers.status import status

router = APIRouter(tags=["lesson"])


class CardAnswer(BaseModel):
    choice: str = ""
    correct: bool | None = None


class Assess(BaseModel):
    score: int


def card_payload(rt: LessonRuntime, ep) -> dict[str, Any]:
    c = rt.current()
    if c is None:
        return {"finished": True}
    step = rt.step_of(c)

    choices = []
    for cid in c.choices:
        try:
            choices.append({"id": cid, **label_of(ep, c.domain, cid)})
        except KeyError:
            continue
    random.shuffle(choices)

    return {
        "finished": False,
        "card_id": c.card_id,
        # 教具三件套：前端按 interaction 分派渲染器，tool_name 直接显示
        "tool": c.tool,
        "tool_name": c.tool_name,
        "interaction": c.interaction,
        "direction": c.direction,
        "domain": c.domain,
        "item_id": c.item_id,
        "step": {"index": step.index, "title": step.title,
                 "minutes": step.minutes, "hint": _hint_for(c)},
        "prompt_audio": url(c.prompt_audio),
        "prompt_audio_slow": url(c.prompt_audio_slow),
        "image": url(c.image),
        "meaning_zh": c.meaning_zh,
        "text": c.text,
        "choices": choices,
        "correct_id": c.correct_id,
        "audio_clips": [url(a) for a in c.audio_clips],
        "needs_answer": c.needs_answer,
        "is_bonus": c.is_bonus,
        "cursor": rt.cursor,
        "total": len(rt.cards),
        "stats": rt.stats,
    }


def _hint_for(c) -> str:
    """操作提示由教具声明，前端不再自己拼。"""
    from ailesson.contract.tools import tool

    return tool(c.tool).hint


@router.post("/api/lesson/{index}/start")
def lesson_start(index: int, restart: bool = False) -> dict[str, Any]:
    """开课。已有该节的未完成快照时**续上**，不重建。

    早先无条件 start_lesson()，等于把暂停快照覆盖掉 —— 退出再进会从第 1 张
    重来，而且卡序会重抽（45 张变 57 张）。传 restart=true 才强制重开。
    """
    s = need_user()

    if not restart and s.runtime is None:
        info = s.paused_info()
        if info and info.get("index") == index:
            s.load()                       # 从盘上快照恢复 runtime
            if s.runtime is not None:
                return card_payload(s.runtime, s.episode)

    if not restart and s.runtime is not None \
            and s.runtime.lesson_index == index and not s.runtime.finished:
        return card_payload(s.runtime, s.episode)

    rt = s.session.start_lesson(index)
    if rt is None:
        raise HTTPException(404, f"没有第 {index} 节课")
    s.runtime = rt
    s.stale_snapshot = None
    s.save()
    return card_payload(rt, s.episode)


@router.get("/api/lesson/current")
def lesson_current() -> dict[str, Any]:
    s = need_lesson()
    return card_payload(s.runtime, s.episode)


@router.post("/api/lesson/answer")
def lesson_answer(body: CardAnswer) -> dict[str, Any]:
    s = need_lesson()
    c = s.runtime.current()
    if c is None:
        return {"finished": True}
    correct = (body.correct if body.correct is not None
               else body.choice == c.correct_id)
    s.runtime.answer(correct=correct)
    s.save()
    return card_payload(s.runtime, s.episode)


@router.post("/api/lesson/advance")
def lesson_advance() -> dict[str, Any]:
    s = need_lesson()
    s.runtime.advance()
    s.save()
    return card_payload(s.runtime, s.episode)


@router.post("/api/lesson/assess")
def lesson_assess(body: Assess) -> dict[str, Any]:
    s = need_lesson()
    s.runtime.self_assess(body.score)
    s.save()
    return card_payload(s.runtime, s.episode)


@router.post("/api/lesson/pause")
def lesson_pause() -> dict[str, Any]:
    """中途退出当前节，进度留在原处，下次从同一张卡续上。"""
    s = need_user()
    if s.runtime is None:
        raise HTTPException(409, "当前没有进行中的课")
    s.save()                      # 快照已含 lesson，落盘后即可安全离开
    s.runtime = None              # 只断开当前视图，不清 completed_lessons
    return status()


@router.post("/api/lesson/finish")
def lesson_finish(narrate: bool = False) -> dict[str, Any]:
    s = S()
    if not s.runtime:
        # 已经收过课了：报告还在，直接回上一份（前端点「写小结」会走到这）
        if s.last_report is not None:
            out = dict(s.last_report)
            if narrate and "narration" not in out:
                out["narration"] = LessonReport.from_dict(
                    out["report"]).narrate(s.llm)
                s.last_report = out
            return out
        raise HTTPException(400, "没有进行中的课")
    rt = s.runtime
    report = s.session.finish_lesson(rt)
    spec = s.session.spec_for(rt.lesson_index)
    mastered = []
    if spec:
        for dom, ids in spec.items().items():
            mastered += [s.session.label_of(dom, i) for i in ids
                         if s.session.progress.is_mastered(dom, i)]
    out = {
        "report": report.to_dict(),
        "text": render_report_text(report),
        "mastered_now": mastered,
    }
    if narrate:
        out["narration"] = report.narrate(s.llm)

    # 课堂数据落 history，供后续调整教学（FR-8）
    if s.uid:
        s.users.append_history(s.uid, {
            "episode_id": s.episode.id,
            "lesson_index": rt.lesson_index,
            "arrangement_id": rt.arrangement.id,
            "arrangement_version": rt.arrangement.version,
            "theme": report.theme,
            "n_words": report.n_words,
            "n_chunks": report.n_chunks,
            "n_sentences": report.n_sentences,
            "asked": report.asked,
            "correct": report.correct,
            "wrong": report.wrong,
            "first_try_correct": report.first_try_correct,
            "accuracy": round(report.accuracy, 3),
            "blind_listen_score": report.blind_listen_score,
            "review_next": report.review_next,
            "demoted": report.demoted,
            "mastered_now": mastered,
        })
        s.users.touch(s.uid)

    s.runtime = None
    s.last_report = out
    s.save()
    return out


class _NullTTS:
    """讲解走文字返回给前端，不需要真的合成语音。"""

    def speak(self, text: str) -> bytes:      # noqa: D102
        return b""


class WrongExplain(BaseModel):
    target: str
    chosen: str
    domain: str = "words"
    correct: bool = False


@router.post("/api/tutor/explain")
def tutor_explain(body: WrongExplain) -> dict[str, str]:
    """答题讲解（FR-6）。藏在「学生看答案」的间隙里，前端异步调。

    答对也讲：可能是蒙对的，而且答对时补搭配 / 语体才让这道题有信息增量。
    讲解一律以**意思**为主 —— 只讲发音等于废话。
    """
    s = S()
    ep = s.episode

    def describe(cid: str) -> tuple[str, str]:
        for getter, attr in ((ep.word, None), (ep.chunk, "text"),
                             (ep.sentence, "text")):
            try:
                obj = getter(cid)
            except KeyError:
                continue
            text = getattr(obj, attr) if attr else cid
            return text, obj.meaning_zh
        return cid, ""

    def example_for(cid: str) -> str:
        """找一句含该词的教学句，给 LLM 当语境。"""
        try:
            ep.word(cid)
        except KeyError:
            return ""
        for sn in ep.sentences:
            if cid in sn.key_words:
                return sn.text
        return ""

    t_text, t_zh = describe(body.target)
    tutor = TutorVoice(llm=s.llm, tts=_NullTTS(), muted=True)

    if body.correct:
        line = tutor.on_confirm(t_text, meaning_zh=t_zh,
                                example=example_for(body.target))
    else:
        c_text, c_zh = describe(body.chosen)
        line = tutor.on_wrong(t_text, c_text, meaning_zh=t_zh, chosen_zh=c_zh,
                              example=example_for(body.target))
        if not line:
            line = f"{t_text}，{t_zh}。" if t_zh else t_text
    return {"line": line}
