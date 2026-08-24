"""共享依赖：全局 AppState 单例 + 路由通用的小工具。

单例放这里而不是 app.py，是为了让各个 router 都能拿到它而不必反向 import app
（那会成环）。测试通过 monkeypatch 这个模块的 DATA / _state 来隔离。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ailesson.server.state import AppState

_state: AppState | None = None


def S() -> AppState:
    """取全局状态，首次访问时才建（建的时候要读素材和凭证，不能在 import 期做）。"""
    global _state
    if _state is None:
        _state = AppState()
    return _state


def reset_state() -> None:
    """丢掉单例。测试和换素材目录时用。"""
    global _state
    _state = None


def need_user() -> AppState:
    """要求已选用户。所有涉及学习数据的接口都得先过这道（§3.1）。"""
    s = S()
    if not s.uid:
        raise HTTPException(409, "还没有用户，先创建一个")
    return s


def need_lesson() -> AppState:
    """要求有进行中的课。"""
    s = S()
    if not s.runtime:
        raise HTTPException(400, "没有进行中的课")
    return s


def url(rel: str) -> str:
    """lesson JSON 里的路径已含 assets/ 前缀，直接挂根上。"""
    return f"/{rel}" if rel else ""


def label_of(ep: Any, dom: str, cid: str) -> dict[str, str]:
    """把内部 id 换成人看得懂的内容 + 素材 URL。

    §4：不露内部 id。`s16` / `guess_what` 用户看不懂。
    """
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


__all__ = ["S", "label_of", "need_lesson", "need_user", "reset_state", "url"]
