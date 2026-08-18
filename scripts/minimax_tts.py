#!/usr/bin/env python3
"""
Minimax 流式 TTS 工具（实测验证版）
用法:
  export MINIMAX_KEY="你的JWT token"
  python3 minimax_tts.py "Good job! Now let's try the word apple." output.mp3

特性:
  - speech-2.6-turbo 流式合成，首包 ~0.3s（直连+连接复用）
  - 音色 yfd-xiaokui-06（少儿向女声）
  - 关键坑: requests.Session + trust_env=False 绕开系统代理（proxy-aws-us 美国代理会导致 10 倍延迟）
"""
import sys, os, time, base64, json, requests, subprocess

GROUP_ID = "1728712324971237986"
VOICE = "yfd-xiaokui-06"
MODEL = "speech-2.6-turbo"

def get_key():
    """优先环境变量 MINIMAX_KEY，否则从 macOS Keychain 读取"""
    key = os.environ.get("MINIMAX_KEY")
    if key:
        return key
    try:
        return subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", "haillelou", "-s", "MINIMAX_KEY", "-w"],
            capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""

def tts_stream(text, out_path=None, verbose=True):
    """合成文本为语音。out_path 给定时保存 mp3，否则只统计耗时。"""
    key = get_key()
    if not key:
        raise SystemExit("未找到 MINIMAX_KEY：请设置环境变量或 `security add-generic-password -a haillelou -s MINIMAX_KEY -w '<key>'`")
    s = requests.Session()
    s.trust_env = False  # 关键：绕开系统代理，否则走美国节点慢 10 倍
    s.headers.update({
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    url = f"https://api.minimaxi.com/v1/t2a_v2?GroupId={GROUP_ID}"
    payload = {
        "model": MODEL, "text": text, "stream": True,
        "timbre_weights": [{"voice_id": VOICE, "weight": 1}],
        "voice_setting": {"voice_id": VOICE, "speed": 1, "pitch": 0, "vol": 1},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        "language_boost": "auto",
    }
    t0 = time.time(); first = None; audio_parts = []
    with s.post(url, json=payload, stream=True, timeout=60) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                try:
                    chunk = json.loads(line[5:]).get("data", {}).get("audio")
                except Exception:
                    continue
                if chunk:
                    if first is None:
                        first = time.time()
                    try:
                        audio_parts.append(bytes.fromhex(chunk))
                    except ValueError:
                        # 兜底：部分接口返回 base64
                        audio_parts.append(base64.b64decode(chunk + "=="))
    ttfb = (first - t0) if first else None
    if verbose:
        print(f"首包={ttfb:.2f}s 总={time.time()-t0:.2f}s 音频≈{sum(len(p) for p in audio_parts)//1024}KB")
    if out_path:
        with open(out_path, "wb") as f:
            f.write(b"".join(audio_parts))
        if verbose:
            print(f"已保存: {out_path}")
    return ttfb, b"".join(audio_parts)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    text = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    tts_stream(text, out)
