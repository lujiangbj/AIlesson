"""组课线：三层勾选 → 听力探测 → 动态挑素材 → 打包成 N 节课。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ailesson.course.probe import (
    PROBE_N,
    build_items,
    calibrate,
    infer_unknown,
    stratified_probe,
)
from ailesson.course.selector import build_pool
from ailesson.infra.llm import LLMError
from ailesson.server.deps import S, need_user, url
from ailesson.server.routers.status import status

router = APIRouter(tags=["course"])


class Checklist(BaseModel):
    words: list[str] = []
    chunks: list[str] = []
    sentences: list[str] = []


@router.get("/api/checklist")
def checklist() -> dict[str, Any]:
    """三层清单铺开，供勾选「哪些我已经会了」。"""
    s = need_user()
    ep = s.episode

    word_groups = s.cache.get_or_build_groups(ep, s.llm)
    chunk_groups = s.cache.get_or_build_item_groups(ep, s.llm, "chunks")
    sent_groups = s.cache.get_or_build_item_groups(ep, s.llm, "sentences")

    return {
        "words": {
            "total": len(ep.words),
            "groups": [
                {"title": g.title, "items": [
                    {"id": w, "label": w, "zh": ep.word(w).meaning_zh,
                     "audio": url(ep.word(w).audio),
                     "image": url("" if ep.word(w).skip_image
                                  else ep.word(w).image)}
                    for w in g.words
                ]}
                for g in word_groups
            ],
        },
        "chunks": {
            "total": len(ep.chunks),
            "groups": [
                {"title": g.title, "items": [
                    {"id": c, "label": ep.chunk(c).text,
                     "zh": ep.chunk(c).meaning_zh,
                     "audio": url(ep.chunk(c).audio_tts),
                     "image": url(ep.chunk(c).image)}
                    for c in g.words
                ]}
                for g in chunk_groups
            ],
        },
        "sentences": {
            "total": len(ep.sentences),
            "groups": [
                {"title": g.title, "items": [
                    {"id": x, "label": ep.sentence(x).text,
                     "zh": ep.sentence(x).meaning_zh,
                     "audio": url(ep.sentence(x).audio_clip),
                     "image": url(ep.sentence(x).image)}
                    for x in g.words
                ]}
                for g in sent_groups
            ],
        },
    }


def _probe_items(ep, known_words: list[str]):
    unknown = {w.lemma for w in ep.words} - set(known_words)
    return build_items(
        [(c.id, c.text) for c in ep.chunks],
        [(s.id, s.text) for s in ep.sentences],
        unknown,
    )


@router.post("/api/checklist/submit")
def checklist_submit(body: Checklist) -> dict[str, Any]:
    """提交勾选 → 动态挑素材 → 打包（慢，前端要显示进度）。

    chunk 和句子不再由用户逐条勾选，而是按 ta 的待学词池现算：挑有教学价值的
    （生词密度合适、地道口语结构、连读点），不追求覆盖所有待学词 —— 词在
    chunk 或句子里被练到就够了。
    """
    s = need_user()
    ep = s.episode

    a = s.session.submit_checklist(
        {"words": body.words, "chunks": body.chunks, "sentences": body.sentences}
    )

    # chunk/句子的待学池来源，优先级从高到低：
    # 1. 用户手动勾选（最直接）
    # 2. 听力探测的实测 + 推断结果（course/probe.py）
    # 3. 按单词池启发式挑（course/selector.py 兜底）
    if not body.chunks and not body.sentences:
        pr = s.session.probe or {}
        probed = pr.get("answers") or {}
        if probed:
            items = _probe_items(ep, pr.get("known_words", []))
            by_id = {i.id: i for i in items}
            cal = calibrate([(by_id[k], v) for k, v in probed.items()
                             if k in by_id])
            inferred = infer_unknown(items, cal, probed=probed)
            # 探测给出的是「听不懂的全集」，再用 selector 按教学价值限量，
            # 否则初学者会得到 71 句 → 打包出几十节课
            pool = build_pool(
                ep, set(a.unknown_words),
                restrict_sentences=set(inferred["sentences"]),
                restrict_chunks=set(inferred["chunks"]),
            )
            source = "probe"
        else:
            pool = build_pool(ep, set(a.unknown_words))
            source = "heuristic"

        # unknown_* 是只读 property，得写底层 dict。同时把没入选的挪回 known ——
        # 否则 validate_plan 会报「不在待学池里」，total_unknown 也会虚高
        for dom, picked in (("sentences", pool["sentences"]),
                            ("chunks", pool["chunks"])):
            keep = [x.id for x in picked]
            dropped = [x for x in a.unknown[dom] if x not in keep]
            a.unknown[dom] = keep
            a.known[dom] = sorted(set(a.known[dom]) | set(dropped))

        s.session.selection = {
            "source": source,
            "sentences": [x.to_dict() for x in pool["sentences"]],
            "chunks": [x.to_dict() for x in pool["chunks"]],
        }

    try:
        s.session.plan = s.cache.get_or_build_plan(ep, a, s.llm)
    except LLMError as e:
        raise HTTPException(500, f"打包失败：{e}") from e
    s.save()
    return status()


@router.post("/api/checklist/preview")
def checklist_preview(body: Checklist) -> dict[str, Any]:
    """只看动态挑出来的素材，不打包。用于调参和前端预览。"""
    s = need_user()
    ep = s.episode
    unknown = {w.lemma for w in ep.words} - set(body.words)
    pool = build_pool(ep, unknown)
    return {
        "unknown_words": len(unknown),
        "sentences": [
            {**x.to_dict(), "text": ep.sentence(x.id).text}
            for x in pool["sentences"]
        ],
        "chunks": [
            {**x.to_dict(), "text": ep.chunk(x.id).text}
            for x in pool["chunks"]
        ],
    }


# ---- 听力探测（chunk / 句子掌握度实测）----


class ProbeStart(BaseModel):
    words: list[str] = []          # 已勾会的单词
    n: int = PROBE_N


@router.post("/api/probe/start")
def probe_start(body: ProbeStart) -> dict[str, Any]:
    """按难度分层抽样，给出要放给用户听的条目。

    为什么要实测：chunk / 句子的听力难度跟单词量不是一回事 —— 认识
    crash / on / couch 每个词，也听不懂 "You gonna crash on the couch?"。
    单纯用单词掌握度推断是猜，这里改成抽样测量。
    """
    s = need_user()
    ep = s.episode
    items = _probe_items(ep, body.words)
    picked = stratified_probe(items, n=body.n)

    s.session.probe = {"known_words": list(body.words),
                       "asked": [i.id for i in picked], "answers": {}}
    s.save()

    def audio_of(it):
        return url(ep.chunk(it.id).audio_tts if it.kind == "chunk"
                   else ep.sentence(it.id).audio_clip)

    return {
        "total_items": len(items),
        "items": [
            # 不给 text —— 要测听力，看到文本就成了阅读题
            {"id": i.id, "kind": i.kind, "audio": audio_of(i),
             "difficulty": round(i.diff, 2)}
            for i in picked
        ],
    }


class ProbeSubmit(BaseModel):
    # id → 是否听懂
    answers: dict[str, bool] = {}


@router.post("/api/probe/submit")
def probe_submit(body: ProbeSubmit) -> dict[str, Any]:
    """收探测结果 → 校准阈值 → 推断其余条目的掌握度。"""
    s = need_user()
    ep = s.episode
    pr = getattr(s.session, "probe", None) or {}
    known_words = pr.get("known_words", [])

    items = _probe_items(ep, known_words)
    by_id = {i.id: i for i in items}
    results = [(by_id[k], v) for k, v in body.answers.items() if k in by_id]

    cal = calibrate(results)
    inferred = infer_unknown(items, cal, probed=body.answers)

    s.session.probe = {**pr, "answers": dict(body.answers),
                       "calibration": cal.to_dict()}
    s.save()

    return {
        "calibration": cal.to_dict(),
        "unknown_chunks": len(inferred["chunks"]),
        "unknown_sentences": len(inferred["sentences"]),
        "chunks": inferred["chunks"],
        "sentences": inferred["sentences"],
        "hint": ("阈值可信，推断结果可用" if cal.confident
                 else "探测样本不足或答案无区分度，用了保守默认阈值"),
    }


@router.post("/api/reset")
def reset() -> dict[str, Any]:
    S().reset()
    return status()
