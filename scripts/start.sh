#!/bin/bash
# 启动 AIlesson。用法: bash scripts/start.sh
cd "$(dirname "$0")/.."

PORT=${AILESSON_PORT:-8791}

# 端口占用就先清掉旧进程（8770 在 macOS 被 sharingd 占，默认已避开）
if lsof -ti ":$PORT" >/dev/null 2>&1; then
  echo "端口 $PORT 被占用，清理旧进程…"
  lsof -ti ":$PORT" | xargs kill -9 2>/dev/null
  sleep 1
fi

echo "AIlesson → http://127.0.0.1:$PORT"
PYTHONPATH=src AILESSON_PORT=$PORT .venv/bin/python -m ailesson.server
