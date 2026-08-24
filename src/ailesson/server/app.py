"""本地 Web 服务：跑一节真实的课。

设计取向：
- 状态在服务端（单用户，进程内 + 落盘），前端只管渲染和上报
- 素材直接从 illit-english-mvp 目录静态托管（NFR-4 零改动）
- LLM 慢活（分组/打包）走 cache.py

启动：
  PYTHONPATH=src .venv/bin/python -m ailesson.server
  → http://127.0.0.1:8791
"""
from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ailesson.course.cache import LLMCache
from ailesson.session import CourseSession
from ailesson.contract.episode import load_episode
from ailesson.classroom.runtime import SEG_BY_INDEX, LessonRuntime
from ailesson.infra.llm import LLMClient, LLMError
from ailesson.course.probe import (
    PROBE_N,
    build_items,
    calibrate,
    infer_unknown,
    stratified_probe,
)
from ailesson.classroom.report import LessonReport, render_report_text
from ailesson.course.selector import build_pool
from ailesson.learner.users import UserStore
from ailesson.classroom.voice import TutorVoice

ROOT = Path(__file__).resolve().parents[3]
MVP_ROOT = Path("/Users/haillelou/Claude/nowordenglish/illit-english-mvp")
DATA = ROOT / "data"
STATE_FILE = DATA / "state.json"
WEB_DIR = ROOT / "web"

# 素材源：peppa 走 MVP 目录，friends 走本项目产出的 lessons/
FRIENDS_LESSONS = DATA / "friends" / "lessons"
FRIENDS_ASSETS = DATA / "friends" / "assets"

CATALOG: dict[str, dict[str, str]] = {
    "peppa-s01e01": {"root": str(MVP_ROOT), "label": "Peppa S1E1（儿童向）"},
    "friends-0101": {"root": str(FRIENDS_LESSONS),
                     "label": "Friends S1E1（成人向）"},
}
DEFAULT_EPISODE = os.environ.get("AILESSON_EPISODE", "peppa-s01e01")
EPISODE_ID = DEFAULT_EPISODE      # 兼容旧引用

logger = logging.getLogger(__name__)

app = FastAPI(title="AIlesson")


def load_catalog_episode(episode_id: str):
    """按 CATALOG 找素材源加载一集。

    peppa 的 lesson JSON 在 MVP 目录，friends 的在本项目 data/friends/lessons，
    两者 schema 一致（friends_lesson.py 做了转换），load_episode 通吃。
    """
    meta = CATALOG.get(episode_id)
    if not meta:
        raise HTTPException(404, f"未知素材 {episode_id}")
    return load_episode(meta["root"], episode_id)


class Store:
    """按用户切换的状态容器。

    素材（Episode）和 LLM 缓存全局共享，只有学习状态分用户。
    """

    def __init__(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.episode_id = DEFAULT_EPISODE
        self.episode = load_catalog_episode(self.episode_id)
        self.users = UserStore(DATA)
        self.cache = LLMCache(self.users.cache_dir())
        self.llm = LLMClient()
        self.session = CourseSession(self.episode, self.llm)
        self.runtime: LessonRuntime | None = None
        self.last_report: dict[str, Any] | None = None   # 收课后仍可补写小结
        self.load()

    # ---- 当前用户 ----

    @property
    def uid(self) -> str | None:
        return self.users.current_id

    def switch(self, uid: str) -> None:
        """切用户：先把当前的存好，再载入目标的。"""
        if self.uid:
            self.save()
        self.users.select(uid)
        self.runtime = None
        self.last_report = None
        self.session = CourseSession(self.episode, self.llm)
        self.load()

    def _is_blank(self) -> bool:
        """会话是不是「什么都没发生」的空态。"""
        return (
            self.session.assessment is None
            and self.session.plan is None
            and not self.session.completed_lessons
            and self.runtime is None
        )

    def save(self) -> None:
        if not self.uid:
            return
        p = self.users.state_path(self.uid)
        # 空会话不许覆盖已有进度 —— 否则「切走再切回」或进程刚起就切用户，
        # 会用内存里的空态把盘上的进度清掉。
        if self._is_blank() and p.exists():
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                self.session.to_dict(lesson_runtime=self.runtime),
                ensure_ascii=False, indent=1,
            )
        )

    def load(self) -> None:
        self.session = CourseSession(self.episode, self.llm)
        self.runtime = None
        if not self.uid:
            return
        p = self.users.state_path(self.uid)
        if not p.exists():
            return
        try:
            snap = json.loads(p.read_text())
        except json.JSONDecodeError:
            return

        # 存档记着它属于哪一集，必须用那一集的素材去 restore。
        # 早先固定用 self.episode（启动时是默认的 peppa），拿 peppa 词表
        # 恢复 Friends 的 plan 会 KeyError（plan 里的 joint 不在 peppa 里）
        saved = snap.get("episode_id")
        if saved and saved != self.episode_id and saved in CATALOG:
            try:
                self.episode = load_catalog_episode(saved)
                self.episode_id = saved
            except Exception:                      # noqa: BLE001
                logger.warning("存档素材 %s 加载失败，保留当前 %s",
                               saved, self.episode_id)

        try:
            self.session, self.runtime = CourseSession.restore(
                self.episode, self.llm, snap)
        except (KeyError, ValueError) as e:
            # 素材换了或条目改名，旧存档对不上。保留文件但从空态开始，
            # 不要让整个服务起不来
            logger.warning("存档与素材不匹配（%s），本次从空态开始：%s",
                           self.episode_id, e)
            self.session = CourseSession(self.episode, self.llm)
            self.runtime = None

    def paused_info(self) -> dict[str, Any] | None:
        """从盘上快照读「暂停在哪」。

        退出时 runtime 被清掉，status 就报不出进度了——但快照还在盘上。
        前端要靠这个显示"进行到 7/45，点这里继续"。
        """
        if not self.uid:
            return None
        p = self.users.state_path(self.uid)
        if not p.exists():
            return None
        try:
            snap = json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
        ls = snap.get("lesson")
        if not ls:
            return None
        # 快照里没有 cards——存的是重建牌所需的输入（review_picked /
        # spot_picked / dir_picked），牌在 restore 时确定性重建。
        # 所以这里给不出 total，只报到第几张；前端按 total 缺失处理
        return {"index": ls.get("lesson_index"), "cursor": ls.get("cursor", 0)}

    def reset(self) -> None:
        """清当前用户的进度，保留 LLM 缓存（那是贵的）。"""
        if self.uid:
            p = self.users.state_path(self.uid)
            if p.exists():
                p.unlink()
        self.session = CourseSession(self.episode, self.llm)
        self.runtime = None
        self.last_report = None


store: Store | None = None


def S() -> Store:
    global store
    if store is None:
        store = Store()
    return store


def url(rel: str) -> str:
    """lesson JSON 里的路径已含 assets/ 前缀，直接挂根上。"""
    return f"/{rel}" if rel else ""


def need_user() -> Store:
    """要求已选用户。所有涉及学习数据的接口都得先过这道。"""
    s = S()
    if not s.uid:
        raise HTTPException(409, "还没有用户，先创建一个")
    return s


# ---- 用户 ----


class NewUser(BaseModel):
    name: str


def user_payload(s: Store) -> dict[str, Any]:
    return {
        "current_id": s.uid,
        "users": [
            {**u.to_dict(), "lessons_done": len(
                json.loads(s.users.state_path(u.id).read_text()).get(
                    "completed_lessons", [])
                if s.users.state_path(u.id).exists() else [])}
            for u in s.users.list()
        ],
    }


@app.get("/api/users")
def users_list() -> dict[str, Any]:
    return user_payload(S())


@app.post("/api/users")
def users_create(body: NewUser) -> dict[str, Any]:
    s = S()
    try:
        u = s.users.create(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # 第一个用户会被自动选中，此时要把状态载进来
    if s.uid == u.id:
        s.load()
    return user_payload(s)


@app.post("/api/users/{uid}/select")
def users_select(uid: str) -> dict[str, Any]:
    s = S()
    try:
        s.switch(uid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    s.users.touch(uid)
    return {**user_payload(s), "status": status()}


@app.delete("/api/users/{uid}")
def users_delete(uid: str) -> dict[str, Any]:
    s = S()
    was_current = s.uid == uid
    try:
        s.users.delete(uid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    if was_current:
        # 删的是当前用户，UserStore 已自动切到别人（或 None），重新载入
        s.runtime = None
        s.last_report = None
        s.load()
    return user_payload(s)


@app.get("/api/users/{uid}/history")
def users_history(uid: str) -> dict[str, Any]:
    s = S()
    if s.users.get(uid) is None:
        raise HTTPException(404, f"没有用户 {uid}")
    return {"history": s.users.history(uid)}


# ---- 状态 ----


@app.get("/api/status")
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
        # fallback=True 表示 LLM 分组失败、用了机械划分。早先这个信号
        # 没暴露到前端，用户拿到"第1组/补充N"的烂课表却不知道原因
        out["plan"] = {"fallback": sess.plan.fallback,
                       "n_lessons": len(sess.plan.lessons)}
        out["lessons"] = [
            {
                "index": l.index,
                "theme": l.theme,
                "words": l.focus_words,
                "chunks": [sess.episode.chunk(c).text for c in l.chunk_ids],
                "sentences": [sess.episode.sentence(x).text for x in l.sentence_ids],
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
        # 退出后 runtime 没了，但盘上快照还在——课程表要显示"点这里继续"
        info = s.paused_info()
        if info and info.get("index") is not None:
            out["lesson_progress"] = {**info, "paused": True}
    return out


# ---- 三层勾选 ----


class Checklist(BaseModel):
    words: list[str] = []
    chunks: list[str] = []
    sentences: list[str] = []


@app.get("/api/checklist")
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
                     "image": url("" if ep.word(w).skip_image else ep.word(w).image)}
                    for w in g.words
                ]}
                for g in word_groups
            ],
        },
        "chunks": {
            "total": len(ep.chunks),
            "groups": [
                {"title": g.title, "items": [
                    {"id": c, "label": ep.chunk(c).text, "zh": ep.chunk(c).meaning_zh,
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


@app.post("/api/checklist/submit")
def checklist_submit(body: Checklist) -> dict[str, Any]:
    """提交勾选 → 动态挑素材 → 打包（慢，前端要显示进度）。

    chunk 和句子不再由用户逐条勾选，而是按 ta 的待学词池现算：
    挑有教学价值的（生词密度合适、地道口语结构、连读点），
    不追求覆盖所有待学词——词在 chunk 或句子里被练到就够了。
    """
    s = need_user()
    ep = s.episode

    a = s.session.submit_checklist(
        {"words": body.words, "chunks": body.chunks, "sentences": body.sentences}
    )

    # chunk/句子的待学池来源，优先级从高到低：
    # 1. 用户手动勾选（最直接）
    # 2. 听力探测的实测+推断结果（probe.py）
    # 3. 按单词池启发式挑（selector.py 兜底）
    if not body.chunks and not body.sentences:
        pr = s.session.probe or {}
        probed = pr.get("answers") or {}
        if probed:
            items = _probe_items(ep, pr.get("known_words", []))
            by_id = {i.id: i for i in items}
            cal = calibrate([(by_id[k], v) for k, v in probed.items()
                             if k in by_id])
            inferred = infer_unknown(items, cal, probed=probed)
            # 探测给出的是"听不懂的全集"，再用 selector 按教学价值限量，
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

        # unknown_* 是只读 property，得写底层 dict。
        # 同时把没入选的挪回 known——否则 validate_plan 会报
        # "不在待学池里"，而 total_unknown 也会虚高
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


@app.post("/api/checklist/preview")
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


def _probe_items(ep, known_words: list[str]):
    unknown = {w.lemma for w in ep.words} - set(known_words)
    return build_items(
        [(c.id, c.text) for c in ep.chunks],
        [(s.id, s.text) for s in ep.sentences],
        unknown,
    )


class ProbeStart(BaseModel):
    words: list[str] = []          # 已勾会的单词
    n: int = PROBE_N


@app.post("/api/probe/start")
def probe_start(body: ProbeStart) -> dict[str, Any]:
    """按难度分层抽样，给出要放给用户听的条目。

    为什么要实测：chunk/句子的听力难度跟单词量不是一回事——认识
    crash/on/couch 每个词，也听不懂 "You gonna crash on the couch?"。
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
            # 不给 text——要测听力，看到文本就成了阅读题
            {"id": i.id, "kind": i.kind, "audio": audio_of(i),
             "difficulty": round(i.diff, 2)}
            for i in picked
        ],
    }


class ProbeSubmit(BaseModel):
    # id → 是否听懂
    answers: dict[str, bool] = {}


@app.post("/api/probe/submit")
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


# ---- 上课 ----


class CardAnswer(BaseModel):
    choice: str = ""
    correct: bool | None = None


class Assess(BaseModel):
    score: int


def card_payload(rt: LessonRuntime, ep) -> dict[str, Any]:
    c = rt.current()
    if c is None:
        return {"finished": True}
    seg = SEG_BY_INDEX[c.segment_index]

    def label_of(dom: str, cid: str) -> dict[str, str]:
        if dom == "words":
            w = ep.word(cid)
            return {"label": cid, "zh": w.meaning_zh, "audio": url(w.audio),
                    "image": url("" if w.skip_image else w.image)}
        if dom == "chunks":
            k = ep.chunk(cid)
            return {"label": k.text, "zh": k.meaning_zh,
                    "audio": url(k.audio_tts), "image": url(k.image)}
        sn = ep.sentence(cid)
        return {"label": sn.text, "zh": sn.meaning_zh,
                "audio": url(sn.audio_clip or sn.audio_tts), "image": url(sn.image)}

    choices = []
    for cid in c.choices:
        try:
            choices.append({"id": cid, **label_of(c.domain, cid)})
        except KeyError:
            continue
    random.shuffle(choices)

    return {
        "finished": False,
        "card_id": c.card_id,
        "kind": c.kind,
        "domain": c.domain,
        "item_id": c.item_id,
        "segment": {"index": seg.index, "title": seg.title, "minutes": seg.minutes},
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


@app.post("/api/lesson/{index}/start")
def lesson_start(index: int, restart: bool = False) -> dict[str, Any]:
    """开课。已有该节的未完成快照时**续上**，不重建。

    早先无条件 start_lesson()，等于把暂停快照覆盖掉——退出再进会从第 1 张
    重来，而且卡序会重抽（复习词和抽检词是随机挑的，45 张变 57 张）。
    传 restart=true 才强制重开。
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
    s.save()
    return card_payload(rt, s.episode)


@app.get("/api/lesson/current")
def lesson_current() -> dict[str, Any]:
    s = S()
    if not s.runtime:
        raise HTTPException(400, "没有进行中的课")
    return card_payload(s.runtime, s.episode)


@app.post("/api/lesson/answer")
def lesson_answer(body: CardAnswer) -> dict[str, Any]:
    s = S()
    if not s.runtime:
        raise HTTPException(400, "没有进行中的课")
    c = s.runtime.current()
    if c is None:
        return {"finished": True}
    correct = body.correct if body.correct is not None else (body.choice == c.correct_id)
    s.runtime.answer(correct=correct)
    s.save()
    return card_payload(s.runtime, s.episode)


@app.post("/api/lesson/advance")
def lesson_advance() -> dict[str, Any]:
    s = S()
    if not s.runtime:
        raise HTTPException(400, "没有进行中的课")
    s.runtime.advance()
    s.save()
    return card_payload(s.runtime, s.episode)


@app.post("/api/lesson/assess")
def lesson_assess(body: Assess) -> dict[str, Any]:
    s = S()
    if not s.runtime:
        raise HTTPException(400, "没有进行中的课")
    s.runtime.self_assess(body.score)
    s.save()
    return card_payload(s.runtime, s.episode)


@app.post("/api/lesson/finish")
def lesson_finish(narrate: bool = False) -> dict[str, Any]:
    s = S()
    if not s.runtime:
        # 已经收过课了：报告还在，直接回上一份（前端点「写小结」会走到这）
        if s.last_report is not None:
            out = dict(s.last_report)
            if narrate and "narration" not in out:
                out["narration"] = LessonReport.from_dict(out["report"]).narrate(s.llm)
                s.last_report = out
            return out
        raise HTTPException(400, "没有进行中的课")
    rt = s.runtime
    report = s.session.finish_lesson(rt)
    spec = s.session.spec_for(rt.lesson_index)
    mastered = []
    if spec:
        for dom, ids in (("words", spec.focus_words), ("chunks", spec.chunk_ids),
                         ("sentences", spec.sentence_ids)):
            mastered += [s.session.label_of(dom, i) for i in ids
                         if s.session.progress.is_mastered(dom, i)]
    out = {
        "report": report.to_dict(),
        "text": render_report_text(report),
        "mastered_now": mastered,
    }
    if narrate:
        out["narration"] = report.narrate(s.llm)

    # 课堂数据落 history，供后续调整教学（PRD §5 FR-8）
    if s.uid:
        s.users.append_history(s.uid, {
            "episode_id": s.episode.id,
            "lesson_index": rt.lesson_index,
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


@app.post("/api/tutor/explain")
def tutor_explain(body: WrongExplain) -> dict[str, str]:
    """答题讲解（FR-6）。藏在「学生看答案」的间隙里，前端异步调。

    答对也讲：可能是蒙对的，而且答对时补搭配/语体才让这道题有信息增量。
    讲解一律以**意思**为主——只讲发音等于废话，学生听到音了但不知道意思。
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


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    S().reset()
    return status()


@app.post("/api/lesson/pause")
def lesson_pause() -> dict[str, Any]:
    """中途退出当前节，进度留在原处，下次从同一张卡续上。

    runtime 快照本来就随 save() 落盘（CourseSession.to_dict 里的 "lesson"），
    restore 也早就支持——缺的只是一个"离开但不丢进度"的出口。
    调试期尤其需要：不然一进课就出不来，只能 reset 整集。
    """
    s = need_user()
    if s.runtime is None:
        raise HTTPException(409, "当前没有进行中的课")
    s.save()                      # 快照已含 lesson，落盘后即可安全离开
    s.runtime = None              # 只断开当前视图，不清 completed_lessons
    return status()


# ---- 素材切换 ----


@app.get("/api/episodes")
def episodes() -> dict[str, Any]:
    """可选素材列表。只列 lesson JSON 真的存在的。"""
    out = []
    for eid, meta in CATALOG.items():
        p = Path(meta["root"]) / f"lesson-{eid}.json"
        if not p.exists():
            continue
        out.append({"id": eid, "label": meta["label"],
                    "current": eid == S().episode_id})
    return {"episodes": out, "current": S().episode_id}


@app.post("/api/episodes/{episode_id}/select")
def select_episode(episode_id: str) -> dict[str, Any]:
    """换素材。会重置当前会话——分诊结果和课程划分都是按集算的。"""
    s = S()
    ep = load_catalog_episode(episode_id)
    s.episode_id, s.episode = episode_id, ep
    s.reset()
    return status()


# ---- 静态资源 ----

app.mount("/assets", StaticFiles(directory=str(MVP_ROOT / "assets")), name="assets")

# Friends 资产：lesson JSON 里的路径形如 friends/0101/cards/spoon.png
if FRIENDS_ASSETS.is_dir():
    app.mount("/friends", StaticFiles(directory=str(FRIENDS_ASSETS)),
              name="friends")


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text())


@app.get("/app.js")
def appjs() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")


def main() -> None:
    import os

    import uvicorn

    # 8770 在 macOS 上被 sharingd (dpap) 占用，默认换到 8791
    port = int(os.environ.get("AILESSON_PORT", "8791"))
    print(f"AIlesson → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
