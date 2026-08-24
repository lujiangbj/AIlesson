#!/usr/bin/env python3
"""
端到端链路测速: ASR(本地whisper) + LLM(deepseek-flash流式) + TTS(Minimax流式)

LLM 选路（LLM_ENDPOINT 环境变量，默认 auto）:
  direct = 直连 model.zhenguanyu.com（公司网络可用，TTFT ~0.56s）
  tunnel = 本地 Model Tunnel 转发（家里可用，TTFT ~1.4s）
  auto   = 先试直连（2s 超时），失败回退 tunnel
"""
import getpass
import json, os, time, base64, requests, urllib.request, subprocess, shlex

VENV = "/tmp/asr_demo_venv/bin/python"
# ===== LLM 配置（从 pi models.json 动态读取 tunnel 配置 + 直连备选）=====
CONF = json.load(open(os.path.expanduser("~/.pi/agent/models.json")))
prov = CONF["providers"]["deepseek-tunnel"]
LLM_MODEL = "deepseek-v4-flash"
TUNNEL_URL = prov["baseUrl"] + "/chat/completions"
DIRECT_URL = "https://model.zhenguanyu.com/v1/chat/completions"
# apiKey 形如 "!/usr/bin/security find-generic-password ... -w"，执行取回
LLM_KEY = subprocess.run(shlex.split(prov["apiKey"][1:]), capture_output=True, text=True).stdout.strip()

# ===== Minimax TTS 配置（Keychain 优先，环境变量兜底）=====
def _tts_key():
    k = os.environ.get("MINIMAX_KEY")
    if k:
        return k
    return subprocess.run(["/usr/bin/security", "find-generic-password", "-a", getpass.getuser(), "-s", "MINIMAX_KEY", "-w"],
        capture_output=True, text=True).stdout.strip()
TTS_KEY = _tts_key()
TTS_GROUP = "1728712324971237986"
TTS_URL = f"https://api.minimaxi.com/v1/t2a_v2?GroupId={TTS_GROUP}"

def tts_stream(text):
    """Minimax 流式 TTS，返回 (首包, 总耗时, chunks, err)"""
    s = requests.Session(); s.trust_env = False
    s.headers.update({"Authorization": f"Bearer {TTS_KEY}", "Content-Type": "application/json",
                      "Accept": "application/json, text/event-stream"})
    payload = {"model": "speech-2.6-turbo", "text": text, "stream": True,
               "timbre_weights": [{"voice_id": "yfd-xiaokui-06", "weight": 1}],
               "voice_setting": {"voice_id": "yfd-xiaokui-06", "speed": 1, "pitch": 0, "vol": 1},
               "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
               "language_boost": "auto"}
    t0 = time.time(); first = None; n = 0
    try:
        with s.post(TTS_URL, json=payload, stream=True, timeout=60) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        if json.loads(line[5:]).get("data", {}).get("audio"):
                            if first is None: first = time.time()
                            n += 1
                    except: pass
    except Exception as e:
        return None, None, None, f"TTS错误: {e}"
    return (first - t0) if first else None, time.time() - t0, n, None

def _llm_stream_once(url, body, timeout=60):
    """单次流式请求，返回 (ttft, total, text, reasoning_done, err)"""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"})
    t0 = time.time(); first = None; text = ""; total = 0; reasoning_done = None; has_reason = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                total = time.time() - t0
                line = raw.decode().strip()
                if not line.startswith("data:"): continue
                if line == "data: [DONE]": break
                try:
                    delta = json.loads(line[5:])["choices"][0]["delta"]
                except: continue
                if delta.get("reasoning_content"):
                    has_reason = True
                if delta.get("content"):
                    if first is None:
                        first = time.time()
                        reasoning_done = first - t0  # 思考结束≈首个回答token
                    text += delta["content"]
        return ((first - t0) if first else None), total, text, (reasoning_done if has_reason else 0), None
    except Exception as e:
        return None, None, None, None, f"LLM错误: {e}"

def llm_stream(prompt, system="You are an English tutor for kids. Reply with one short encouraging sentence + one short question. Keep it under 15 words."):
    """LLM 流式：auto 选路（直连优先，失败回退 tunnel），返回首token时间和总耗时"""
    body = {"model": LLM_MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}], "stream": True, "max_tokens": 400, "thinking": {"type": "disabled"}}
    mode = os.environ.get("LLM_ENDPOINT", "auto")
    if mode == "direct":
        return _llm_stream_once(DIRECT_URL, body)
    if mode == "tunnel":
        return _llm_stream_once(TUNNEL_URL, body)
    # auto: 先试直连（短超时，公司网络可直连 0.56s），失败回退 tunnel
    res = _llm_stream_once(DIRECT_URL, body, timeout=2)
    if res[4] is None:
        return res
    return _llm_stream_once(TUNNEL_URL, body)

def main():
    import subprocess
    # 1. 生成测试音频（学生发言）
    subprocess.run(["say", "-v", "Samantha", "-o", "/tmp/e2e_student.aiff", "Good morning, teacher"], capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "/tmp/e2e_student.aiff", "-ar", "16000", "-ac", "1", "/tmp/e2e_student.wav"], capture_output=True)

    print("========== 链路1: ASR（本地whisper识别学生发言）==========")
    t0 = time.time()
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    t_load = time.time() - t0
    t0 = time.time()
    segs, info = model.transcribe("/tmp/e2e_student.wav", language="en")
    student_text = " ".join(s.text.strip() for s in segs)
    t_asr = time.time() - t0
    print(f"  模型加载={t_load:.1f}s（一次性） 识别={t_asr:.2f}s → 学生说: {student_text!r}")

    print("========== 链路2: LLM（deepseek-flash 流式）==========")
    ttft, total, reply, think_t, err = llm_stream(f'Student just said: "{student_text}". Reply as tutor.')
    if err: print(" ", err); return
    print(f"  路由={os.environ.get('LLM_ENDPOINT', 'auto')} 思考结束={think_t:.2f}s 首token={ttft:.2f}s 总={total:.2f}s → Tutor回复: {reply!r}")

    print("========== 链路3: TTS（Minimax turbo 流式）==========")
    ttfb, total, n, err = tts_stream(reply)
    if err: print(" ", err); return
    print(f"  首包={ttfb:.2f}s 总={total:.2f}s chunks={n}")

    print("\n========== 端到端汇总 ==========")
    print(f"  学生说完 → 出文本(ASR):     {t_asr:.2f}s")
    print(f"  → 回复首token(LLM TTFT):   {ttft:.2f}s（含思考{think_t:.2f}s）")
    print(f"  → 语音首包(TTS TTFB):      {ttfb:.2f}s")
    print(f"  全链路（说完→开口说话）:    {t_asr + ttft + ttfb:.2f}s")

if __name__ == "__main__":
    main()
