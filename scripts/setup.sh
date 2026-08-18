#!/bin/bash
# AIlesson 环境安装脚本（Mac arm64, Python 3.12）
set -e

PY=${PYTHON:-python3}

echo "==> 创建虚拟环境"
$PY -m venv .venv
source .venv/bin/activate

echo "==> 安装 ASR + 发音评测依赖（注意：torch 需单独用 pytorch 源装，否则找不到包）"
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install faster-whisper transformers soundfile phonemizer requests

echo "==> 安装 espeak-ng（参考音素生成）"
which espeak-ng || brew install espeak-ng

echo "==> 安装 Minimax TTS 依赖"
pip install requests

echo "==> 完成。首次运行会下载模型："
echo "    whisper-base (~150MB) / wav2vec2 音素模型 (~300MB)"
echo "    模型缓存到 ~/.cache/huggingface"
