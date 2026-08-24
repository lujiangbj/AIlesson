"""用户名册：创建 / 选择 / 删除 / 课堂历史。

MVP 无密码（FR-7）。删用户 = 删他的 state + history，不可恢复。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ailesson.server.deps import S
from ailesson.server.state import AppState

router = APIRouter(prefix="/api/users", tags=["users"])


class NewUser(BaseModel):
    name: str


def user_payload(s: AppState) -> dict[str, Any]:
    return {
        "current_id": s.uid,
        "users": [
            {**u.to_dict(), "lessons_done": s.learner.lessons_done(u.id)}
            for u in s.users.list()
        ],
    }


@router.get("")
def users_list() -> dict[str, Any]:
    return user_payload(S())


@router.post("")
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


@router.post("/{uid}/select")
def users_select(uid: str) -> dict[str, Any]:
    from ailesson.server.routers.status import status

    s = S()
    try:
        s.switch(uid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    s.users.touch(uid)
    return {**user_payload(s), "status": status()}


@router.delete("/{uid}")
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


@router.get("/{uid}/history")
def users_history(uid: str) -> dict[str, Any]:
    s = S()
    if s.users.get(uid) is None:
        raise HTTPException(404, f"没有用户 {uid}")
    return {"history": s.users.history(uid)}
