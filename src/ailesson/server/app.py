"""FastAPI 装配：挂 router、挂静态资源、起服务。

业务逻辑不在这里。状态在 state.py，路由在 routers/ 下按关注点分文件。
原先这个文件 930 行，一个文件里同时管用户 CRUD、勾选、探测、课堂运行时、
tutor 语音、素材目录和静态托管。
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ailesson.server import state as st
from ailesson.server.routers import (
    admin, content, course, lesson, segments, status, users,
)

app = FastAPI(title="AIlesson")

for r in (users.router, status.router, course.router, lesson.router,
          content.router, segments.router, admin.router):
    app.include_router(r)


# ---- 静态资源 ----

if st.MVP_ROOT.is_dir():
    app.mount("/assets", StaticFiles(directory=str(st.MVP_ROOT / "assets")),
              name="assets")

# Friends 资产：lesson JSON 里的路径形如 friends/0101/cards/spoon.png
if st.FRIENDS_ASSETS.is_dir():
    app.mount("/friends", StaticFiles(directory=str(st.FRIENDS_ASSETS)),
              name="friends")


# ---- 前端 ----
#
# 两个入口：学习者端（/）和后台（/admin）。它们的迭代节奏差得最远 ——
# 教室端要上 iPad、要对延迟负责；后台是自己看的，糙一点没关系。

@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((st.WEB_DIR / "index.html").read_text())


@app.get("/app.js")
def appjs() -> FileResponse:
    return FileResponse(st.WEB_DIR / "app.js",
                        media_type="application/javascript")


@app.get("/admin")
def admin_page() -> HTMLResponse:
    return HTMLResponse((st.WEB_DIR / "admin" / "index.html").read_text())


@app.get("/admin/admin.js")
def admin_js() -> FileResponse:
    return FileResponse(st.WEB_DIR / "admin" / "admin.js",
                        media_type="application/javascript")


def main() -> None:
    import uvicorn

    # 8770 在 macOS 上被 sharingd (dpap) 占用，默认换到 8791
    port = int(os.environ.get("AILESSON_PORT", "8791"))
    print(f"AIlesson  学习者 → http://127.0.0.1:{port}")
    print(f"          后台   → http://127.0.0.1:{port}/admin")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
