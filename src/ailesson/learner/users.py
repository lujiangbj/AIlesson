"""用户系统（MVP：无密码）。

课程是高度个性化的 —— 每个人的待学池、掌握度、课堂数据都不一样，所以必须分用户
存。但 MVP 阶段只有几个人测试，不做密码/登录，只做「创建 / 删除 / 选择」。

存储布局：

    data/
      users.json                   用户名册 + 当前选中
      users/<uid>/state.json       学习状态（勾选、课程表、进度）
      users/<uid>/history.jsonl    每节课的课堂数据（供后续调整教学）
      cache/                       LLM 缓存 —— **全局共享，不分用户**

为什么缓存不分用户：词表分组是全集共享的；打包结果按「待学池内容」哈希，
两个人勾出同样的池子可以直接复用。分用户会白烧钱。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAFE = re.compile(r"[^a-z0-9]+")


@dataclass
class User:
    id: str
    name: str
    created_at: int
    last_active: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> User:
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            created_at=d.get("created_at", 0),
            last_active=d.get("last_active", 0),
        )


def _make_id(name: str) -> str:
    """人可读 + 唯一。名字里的斜杠等一律洗掉，id 要能安全当目录名。"""
    slug = SAFE.sub("-", name.strip().lower()).strip("-")[:16]
    return f"{slug}-{uuid.uuid4().hex[:6]}" if slug else uuid.uuid4().hex[:12]


class UserStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.users: list[User] = []
        self.current_id: str | None = None
        self._load()
        self._migrate_legacy()

    # ---- 路径 ----

    @property
    def index_file(self) -> Path:
        return self.root / "users.json"

    def user_dir(self, uid: str) -> Path:
        return self.root / "users" / uid

    def state_path(self, uid: str) -> Path:
        return self.user_dir(uid) / "state.json"

    def history_path(self, uid: str) -> Path:
        return self.user_dir(uid) / "history.jsonl"

    def cache_dir(self) -> Path:
        """LLM 缓存全局共享，不带 uid。"""
        return self.root / "cache"

    # ---- 名册 ----

    def _load(self) -> None:
        if not self.index_file.exists():
            return
        try:
            d = json.loads(self.index_file.read_text())
        except json.JSONDecodeError:
            return
        self.users = [User.from_dict(x) for x in d.get("users", [])]
        self.current_id = d.get("current_id")
        if self.current_id and not self.get(self.current_id):
            self.current_id = self.users[0].id if self.users else None

    def _save(self) -> None:
        self.index_file.write_text(
            json.dumps(
                {"users": [u.to_dict() for u in self.users],
                 "current_id": self.current_id},
                ensure_ascii=False, indent=1,
            )
        )

    def _migrate_legacy(self) -> None:
        """老的单用户 data/state.json 收进第一个用户，别丢进度。"""
        legacy = self.root / "state.json"
        if not legacy.exists() or self.users:
            return
        try:
            snap = json.loads(legacy.read_text())
        except json.JSONDecodeError:
            return
        u = self.create("我")
        p = self.state_path(u.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
        legacy.unlink()      # 移走，避免下次重复迁移

    # ---- 增删选 ----

    def list(self) -> list[User]:
        return list(self.users)

    def get(self, uid: str) -> User | None:
        return next((u for u in self.users if u.id == uid), None)

    def create(self, name: str) -> User:
        name = (name or "").strip()
        if not name:
            raise ValueError("名字不能为空")
        u = User(id=_make_id(name), name=name, created_at=int(time.time()))
        self.users.append(u)
        self.user_dir(u.id).mkdir(parents=True, exist_ok=True)
        if self.current_id is None:      # 第一个用户自动选中
            self.current_id = u.id
        self._save()
        return u

    def select(self, uid: str) -> User:
        u = self.get(uid)
        if u is None:
            raise KeyError(f"没有用户 {uid}")
        self.current_id = uid
        self._save()
        return u

    def current(self) -> User | None:
        return self.get(self.current_id) if self.current_id else None

    def delete(self, uid: str) -> None:
        u = self.get(uid)
        if u is None:
            raise KeyError(f"没有用户 {uid}")
        self.users.remove(u)
        shutil.rmtree(self.user_dir(uid), ignore_errors=True)
        if self.current_id == uid:
            self.current_id = self.users[0].id if self.users else None
        self._save()

    def touch(self, uid: str) -> None:
        u = self.get(uid)
        if u:
            u.last_active = int(time.time())
            self._save()

    # ---- 课堂数据 ----

    def append_history(self, uid: str, record: dict[str, Any]) -> None:
        """记一节课的数据。JSONL 追加写 —— 只增不改，天然抗并发。"""
        p = self.history_path(uid)
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": int(time.time()), **record}
        with p.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def history(self, uid: str) -> list[dict[str, Any]]:
        p = self.history_path(uid)
        if not p.exists():
            return []
        out = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # 坏行跳过，不让一行毁掉整份历史
        return out
