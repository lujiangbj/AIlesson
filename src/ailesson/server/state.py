"""应用状态：把原来的 god object `Store` 拆成三块。

原先 `Store` 一身四职 —— 素材目录、用户名册、LLM 缓存、当前会话 + 运行时，
切用户要同时协调六件事，`_is_blank()` 这类补丁就是这么长出来的。

现在：

    ContentRepo   素材从哪来（目录 + 加载），无状态，可被后台直接问
    LearnerStore  学习者数据的读写（名册 + 存档 + 课堂历史）
    AppState      当前选中的用户 / 素材 / 会话 / 运行时，只管「此刻在干什么」

存档的 save / load 从 AppState 挪到 LearnerStore：那是持久化的事，
不该和「当前在上第几节」混在一个类里。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ailesson.classroom.arrangement import DEFAULT, Arrangement, compatible
from ailesson.classroom.runtime import LessonRuntime
from ailesson.contract.episode import Episode, load_episode
from ailesson.course.cache import LLMCache
from ailesson.infra.llm import LLMClient
from ailesson.learner.users import UserStore
from ailesson.session import CourseSession

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]

# 素材源。Peppa 走 MVP 目录（只读，NFR-4），Friends 走本项目产出的 lessons/
MVP_ROOT = Path(os.environ.get(
    "AILESSON_MVP_ROOT",
    "/Users/haillelou/Claude/nowordenglish/illit-english-mvp",
))

DATA = ROOT / "data"
WEB_DIR = ROOT / "web"
FRIENDS_LESSONS = DATA / "friends" / "lessons"
FRIENDS_ASSETS = DATA / "friends" / "assets"

DEFAULT_EPISODE = os.environ.get("AILESSON_EPISODE", "peppa-s01e01")


class ContentRepo:
    """素材目录。教研后台和教室端都问它要素材，它不知道学习者的存在。"""

    def __init__(self, mvp_root: Path | None = None,
                 friends_root: Path | None = None) -> None:
        self.mvp_root = Path(mvp_root or MVP_ROOT)
        self.friends_root = Path(friends_root or FRIENDS_LESSONS)

    def catalog(self) -> dict[str, dict[str, str]]:
        return {
            "peppa-s01e01": {"root": str(self.mvp_root),
                             "label": "Peppa S1E1（儿童向）"},
            "friends-0101": {"root": str(self.friends_root),
                             "label": "Friends S1E1（成人向）"},
        }

    def lesson_path(self, episode_id: str) -> Path | None:
        meta = self.catalog().get(episode_id)
        if not meta:
            return None
        return Path(meta["root"]) / f"lesson-{episode_id}.json"

    def available(self) -> list[dict[str, str]]:
        """只列 lesson JSON 真的存在的。"""
        out = []
        for eid, meta in self.catalog().items():
            p = self.lesson_path(eid)
            if p and p.exists():
                out.append({"id": eid, "label": meta["label"]})
        return out

    def load(self, episode_id: str) -> Episode:
        """按目录找素材源加载一集。

        peppa 的 lesson JSON 在 MVP 目录，friends 的在 data/friends/lessons，
        两者 schema 一致（content/friends_lesson.py 做了转换），load_episode 通吃。
        """
        meta = self.catalog().get(episode_id)
        if not meta:
            raise KeyError(f"未知素材 {episode_id}")
        return load_episode(meta["root"], episode_id)


class LearnerStore:
    """学习者数据的读写。名册委托给 UserStore，这里管存档和历史。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users = UserStore(self.data_dir)

    @property
    def current_id(self) -> str | None:
        return self.users.current_id

    def cache_dir(self) -> Path:
        return self.users.cache_dir()

    def state_path(self, uid: str) -> Path:
        return self.users.state_path(uid)

    def read_snapshot(self, uid: str) -> dict[str, Any] | None:
        p = self.state_path(uid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            logger.warning("存档坏了，忽略：%s", p)
            return None

    def write_snapshot(self, uid: str, snap: dict[str, Any]) -> None:
        p = self.state_path(uid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(snap, ensure_ascii=False, indent=1))

    def clear_snapshot(self, uid: str) -> None:
        p = self.state_path(uid)
        if p.exists():
            p.unlink()

    def lessons_done(self, uid: str) -> int:
        snap = self.read_snapshot(uid) or {}
        return len(snap.get("completed_lessons", []))


class AppState:
    """此刻在干什么：哪个用户、哪一集、会话进行到哪、在上哪节课。"""

    def __init__(
        self,
        data_dir: Path | None = None,
        repo: ContentRepo | None = None,
        llm: Any | None = None,
        arrangement: Arrangement = DEFAULT,
    ) -> None:
        self.repo = repo or ContentRepo()
        self.learner = LearnerStore(data_dir or DATA)
        self.cache = LLMCache(self.learner.cache_dir())
        self.llm = llm or LLMClient()
        self.arrangement = arrangement
        self.episode_id = DEFAULT_EPISODE
        self.episode = self.repo.load(self.episode_id)
        self.session = CourseSession(self.episode, self.llm)
        self.runtime: LessonRuntime | None = None
        self.last_report: dict[str, Any] | None = None   # 收课后仍可补写小结
        # 编排与快照不兼容时置位，前端据此提示「重开还是按旧编排续」
        self.stale_snapshot: dict[str, Any] | None = None
        self.load()

    # ---- 当前用户 ----

    @property
    def uid(self) -> str | None:
        return self.learner.current_id

    @property
    def users(self) -> UserStore:
        """名册。路由层直接用，不必绕两层。"""
        return self.learner.users

    def switch(self, uid: str) -> None:
        """切用户：先把当前的存好，再载入目标的。"""
        if self.uid:
            self.save()
        self.learner.users.select(uid)
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
        # 空会话不许覆盖已有进度 —— 否则「切走再切回」或进程刚起就切用户，
        # 会用内存里的空态把盘上的进度清掉（§10.12）
        if self._is_blank() and self.learner.state_path(self.uid).exists():
            return
        self.learner.write_snapshot(
            self.uid, self.session.to_dict(lesson_runtime=self.runtime)
        )

    def load(self) -> None:
        self.session = CourseSession(self.episode, self.llm)
        self.runtime = None
        self.stale_snapshot = None
        if not self.uid:
            return
        snap = self.learner.read_snapshot(self.uid)
        if snap is None:
            return

        # 存档记着它属于哪一集，必须用那一集的素材去 restore。
        # 用当前素材恢复别集的 plan 会 KeyError（plan 里的条目不在这一集里）
        saved = snap.get("episode_id")
        if saved and saved != self.episode_id:
            try:
                self.episode = self.repo.load(saved)
                self.episode_id = saved
            except Exception:                      # noqa: BLE001
                logger.warning("存档素材 %s 加载失败，保留当前 %s",
                               saved, self.episode_id)

        # 编排换了就不许重建：卡序是按编排确定性重算的，拿新编排恢复旧快照
        # 会得到另一副牌，续上错位且不报错（§10.5 的延伸）
        lsnap = snap.get("lesson")
        if lsnap and not compatible(self.arrangement, lsnap):
            self.stale_snapshot = {
                "lesson_index": lsnap.get("lesson_index"),
                "cursor": lsnap.get("cursor", 0),
                "arrangement_id": lsnap.get("arrangement_id"),
                "arrangement_version": lsnap.get("arrangement_version"),
            }
            logger.warning(
                "第 %s 节的快照跑在编排 %s v%s 上，当前是 %s v%s，不重建课堂",
                lsnap.get("lesson_index"), lsnap.get("arrangement_id"),
                lsnap.get("arrangement_version"),
                self.arrangement.id, self.arrangement.version,
            )
            snap = {**snap, "lesson": None}

        try:
            self.session, self.runtime = CourseSession.restore(
                self.episode, self.llm, snap, arrangement=self.arrangement)
        except (KeyError, ValueError) as e:
            # 素材换了或条目改名，旧存档对不上。保留文件但从空态开始，
            # 不要让整个服务起不来
            logger.warning("存档与素材不匹配（%s），本次从空态开始：%s",
                           self.episode_id, e)
            self.session = CourseSession(self.episode, self.llm)
            self.runtime = None

    def paused_info(self) -> dict[str, Any] | None:
        """从盘上快照读「暂停在哪」。

        退出时 runtime 被清掉，status 就报不出进度了 —— 但快照还在盘上。
        前端要靠这个显示「进行到 7/45，点这里继续」。
        """
        if not self.uid:
            return None
        snap = self.learner.read_snapshot(self.uid)
        ls = (snap or {}).get("lesson")
        if not ls:
            return None
        # 快照里没有 cards —— 存的是重建牌所需的输入，牌在 restore 时重建。
        # 所以这里给不出 total，前端按 total 缺失处理
        return {"index": ls.get("lesson_index"), "cursor": ls.get("cursor", 0)}

    def select_episode(self, episode_id: str) -> None:
        """换素材。会重置当前会话 —— 自评和课程划分都是按集算的。"""
        self.episode = self.repo.load(episode_id)
        self.episode_id = episode_id
        self.reset()

    def reset(self) -> None:
        """清当前用户的进度，保留 LLM 缓存（那是贵的）。"""
        if self.uid:
            self.learner.clear_snapshot(self.uid)
        self.session = CourseSession(self.episode, self.llm)
        self.runtime = None
        self.last_report = None
        self.stale_snapshot = None


__all__ = [
    "DATA", "DEFAULT_EPISODE", "FRIENDS_ASSETS", "FRIENDS_LESSONS",
    "MVP_ROOT", "ROOT", "WEB_DIR",
    "AppState", "ContentRepo", "LearnerStore",
]
