#!/usr/bin/env python3
"""生产发音示范音：单词 + 例句。

引擎默认 GPT-4o-mini-TTS（网关 azure-openai-gpt-4o-mini-tts），
因为它的发音更 native。

⚠️ 引擎选择的教训：我曾用 ASR 回环（合成→识别→比对还原率）做客观对比，
40 词头对头结果 Minimax 39/40 优于 GPT 38/40，据此建议不换。但 ASR 回环
只能证明"机器听得清"，**测不出母语感和自然度**——那需要人耳。用户听后
判定 GPT 更 native，采纳。ASR 回环仍保留（--verify）用于抓"念错词"这类
硬错误，但不作为引擎优劣的依据。

单词用 speed=0.9 略放慢，起音更清楚；例句用 1.0 保持自然语流。
GPT 的 instructions 参数可控风格，实测带"清晰咬字"指令后 alloy 从 7/10
升到 9/10，但对不同音色作用方向不一致（fable 反而 7→5）。

用法:
    python3 scripts/gen_audio.py 0101                 # 全量（词 + 例句）
    python3 scripts/gen_audio.py 0101 --limit 5       # 先跑 5 个试水
    python3 scripts/gen_audio.py 0101 --verify        # ASR 回环校验
    python3 scripts/gen_audio.py 0101 --engine minimax  # 切回 Minimax
    python3 scripts/gen_audio.py 0101 --voice nova --force  # 换音色重生成
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import requests  # noqa: E402
from minimax_tts import GROUP_ID, get_key  # noqa: E402

ASSETS = ROOT / "data" / "friends" / "assets"

# GPT 线（默认）——网关代理的 Azure OpenAI，OpenAI 兼容协议
GPT_KEY = "sk-mg-jxstraewhhaaknp5dq3lupbwfmtcbhrlegzqevy"
GPT_API = "https://model.zhenguanyu.com/v1/audio/speech"
GPT_MODEL = "azure-openai-gpt-4o-mini-tts"
GPT_VOICE = "alloy"
# instructions 控制风格。gpt-4o-mini-tts 特有，标准 tts-1 没这个参数
GPT_INSTRUCTIONS = (
    "Speak as a clear English pronunciation model for a language learner: "
    "articulate every consonant crisply, especially initial consonant "
    "clusters and final consonants. Neutral American accent, calm and "
    "slightly slow."
)

# Minimax 线（备选）
MM_MODEL = "speech-2.6-turbo"
MM_VOICE = "English_Gentle-voiced_man"

WORD_SPEED = 0.9       # 单词略放慢，起音清楚
SENT_SPEED = 1.0       # 例句保持自然语流
RETRIES = 3


def session(engine: str) -> requests.Session:
    s = requests.Session()
    # 关键坑：公司代理 proxy-aws-us 走美国节点，TTS 慢 10 倍
    s.trust_env = False
    key = GPT_KEY if engine == "gpt" else get_key()
    s.headers.update({"Authorization": f"Bearer {key}",
                      "Content-Type": "application/json"})
    return s


# 空音频判定：实测坏样本恰好 7680 bytes / 0.48s（无论什么词都这个大小），
# 而正常单词至少 1s 以上。按字符数估个下限，比固定阈值可靠
MIN_BYTES_PER_CHAR = 900
MIN_BYTES = 9000


def _too_short(text: str, size: int) -> bool:
    floor = max(MIN_BYTES, min(len(text), 12) * MIN_BYTES_PER_CHAR)
    return size < floor


def _synth_gpt(sess, text, out, speed, voice) -> tuple[bool, str | None]:
    body = {
        "model": GPT_MODEL, "input": text, "voice": voice,
        "response_format": "mp3", "speed": speed,
        "instructions": GPT_INSTRUCTIONS,
    }
    r = sess.post(GPT_API, json=body, timeout=90)
    if r.status_code != 200:
        return False, f"http {r.status_code}: {r.text[:100]}"
    if _too_short(text, len(r.content)):
        return False, f"空音频 {len(r.content)}B（重试）"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    return True, None


def _synth_minimax(sess, text, out, speed, voice) -> tuple[bool, str | None]:
    payload = {
        "model": MM_MODEL, "text": text, "stream": False,
        "voice_setting": {"voice_id": voice, "speed": speed,
                          "pitch": 0, "vol": 1},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000,
                          "format": "mp3", "channel": 1},
        "language_boost": "English",
    }
    r = sess.post(f"https://api.minimaxi.com/v1/t2a_v2?GroupId={GROUP_ID}",
                  json=payload, timeout=60)
    d = r.json()
    audio = (d.get("data") or {}).get("audio")
    if audio:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(bytes.fromhex(audio))
        return True, None
    return False, str(d.get("base_resp"))[:120]


def synth(sess: requests.Session, text: str, out: Path, speed: float = 1.0,
          *, engine: str = "gpt", voice: str | None = None,
          ) -> tuple[bool, float, str | None]:
    fn = _synth_gpt if engine == "gpt" else _synth_minimax
    v = voice or (GPT_VOICE if engine == "gpt" else MM_VOICE)
    last = None
    for attempt in range(RETRIES):
        t0 = time.time()
        try:
            ok, err = fn(sess, text, out, speed, v)
            if ok:
                return True, time.time() - t0, None
            last = err
        except Exception as e:                       # noqa: BLE001
            last = str(e)[:120]
        time.sleep(1.5 * (attempt + 1))
    return False, 0.0, last


def load_targets(ep_id: str) -> list[dict]:
    """取该集所有可教学的词（含判 no 的——它们仍要跟读和句子听辨）。"""
    p = ASSETS / ep_id / "pickable.json"
    if not p.exists():
        sys.exit(f"没有 {p}，先跑 scripts/gen_cards.py {ep_id} --judge")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["verdicts"]


# MVP lesson schema 里 chunk/sentence 都有 audio_tts + audio_tts_slow 两版。
# 慢速版用于跟读环节，学习者需要听清每个音
SLOW_SPEED = 0.75


def do_lesson_audio(ep_id: str, engine: str, voice: str, force: bool) -> int:
    """给 chunk 和教学句生成音频（normal + slow 两版）。"""
    ep_dir = ASSETS / ep_id
    lp = ep_dir / "lesson.json"
    if not lp.exists():
        sys.exit(f"先跑: python3 scripts/gen_lesson.py {ep_id} --extract")
    lesson = json.loads(lp.read_text(encoding="utf-8"))

    mpath = ep_dir / "audio.json"
    manifest = (json.loads(mpath.read_text(encoding="utf-8"))
                if mpath.exists() else {"episode_id": ep_id})
    manifest.setdefault("chunks", {})
    manifest.setdefault("lesson_sentences", {})
    if force:
        manifest["chunks"], manifest["lesson_sentences"] = {}, {}

    jobs: list[tuple[str, str, str, Path, float]] = []
    for c in lesson["chunks"]:
        if c["id"] in manifest["chunks"]:
            continue
        base = ep_dir / "audio" / "chunks"
        jobs.append(("chunks", c["id"], c["text"],
                     base / f"{c['id']}.mp3", SENT_SPEED))
        jobs.append(("chunks_slow", c["id"], c["text"],
                     base / f"{c['id']}_slow.mp3", SLOW_SPEED))
    for s in lesson["sentences"]:
        if s["id"] in manifest["lesson_sentences"]:
            continue
        base = ep_dir / "audio" / "lesson_sentences"
        jobs.append(("lesson_sentences", s["id"], s["text"],
                     base / f"{s['id']}.mp3", SENT_SPEED))
        jobs.append(("lesson_sentences_slow", s["id"], s["text"],
                     base / f"{s['id']}_slow.mp3", SLOW_SPEED))

    print(f"{len(lesson['chunks'])} chunk + {len(lesson['sentences'])} 句 "
          f"→ {len(jobs)} 个音频待生成 · {engine}/{voice}", flush=True)

    sess = session(engine)
    ok = fail = 0
    for i, (kind, key, text, path, speed) in enumerate(jobs, 1):
        good, _, err = synth(sess, text, path, speed,
                             engine=engine, voice=voice)
        if not good:
            print(f"[{i}/{len(jobs)}] {kind}/{key} 失败: {err}", flush=True)
            fail += 1
            continue
        slot = kind.replace("_slow", "")
        entry = manifest[slot].setdefault(key, {"text": text})
        field = "audio_tts_slow" if kind.endswith("_slow") else "audio_tts"
        entry[field] = str(path.relative_to(ep_dir))
        ok += 1
        if i % 40 == 0 or i == len(jobs):
            mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            print(f"[{i}/{len(jobs)}] 完成 {ok} 失败 {fail}", flush=True)

    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\nchunk {len(manifest['chunks'])} · "
          f"句 {len(manifest['lesson_sentences'])} · 失败 {fail}", flush=True)
    return 0 if fail == 0 else 1


def do_gen(ep_id: str, limit: int | None, engine: str, voice: str,
           force: bool) -> int:
    rows = load_targets(ep_id)
    ep_dir = ASSETS / ep_id
    words_dir, sents_dir = ep_dir / "audio" / "words", ep_dir / "audio" / "sentences"
    mpath = ep_dir / "audio.json"
    manifest = (json.loads(mpath.read_text(encoding="utf-8"))
                if mpath.exists() and not force
                else {"episode_id": ep_id, "words": {}, "sentences": {}})
    manifest["engine"], manifest["voice"] = engine, voice

    todo = [r for r in rows if force or r["word"] not in manifest["words"]]
    if limit:
        todo = todo[:limit]
    if force:
        manifest["words"], manifest["sentences"] = {}, {}

    n_sent = sum(len(r.get("examples") or []) for r in todo)
    print(f"{len(rows)} 个词 · 待生成 {len(todo)} 词 + {n_sent} 句 "
          f"· {engine}/{voice}", flush=True)

    sess = session(engine)
    ok = fail = 0
    for i, r in enumerate(todo, 1):
        w = r["word"]
        wp = words_dir / f"{w}.mp3"
        good, el, err = synth(sess, w, wp, WORD_SPEED,
                              engine=engine, voice=voice)
        if not good:
            print(f"[{i}/{len(todo)}] {w} 失败: {err}", flush=True)
            fail += 1
            continue
        manifest["words"][w] = {
            "file": str(wp.relative_to(ep_dir)), "speed": WORD_SPEED}

        # 例句：一个词最多 2 句，文件名带序号
        for j, ex in enumerate(r.get("examples") or [], 1):
            sp = sents_dir / f"{w}-{j}.mp3"
            sgood, _, serr = synth(sess, ex, sp, SENT_SPEED,
                                   engine=engine, voice=voice)
            if sgood:
                manifest["sentences"].setdefault(w, []).append({
                    "file": str(sp.relative_to(ep_dir)), "text": ex})
            else:
                print(f"    句失败 {w}-{j}: {serr}", flush=True)

        ok += 1
        if i % 20 == 0 or i == len(todo):
            mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            print(f"[{i}/{len(todo)}] {w} · 累计 {len(manifest['words'])} 词 "
                  f"/ {sum(len(v) for v in manifest['sentences'].values())} 句",
                  flush=True)

    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\n完成 {ok} 词，失败 {fail}", flush=True)
    print(f"→ {(ep_dir / 'audio').relative_to(ROOT)}/", flush=True)
    return 0 if fail == 0 else 1


def do_verify(ep_id: str, limit: int | None) -> int:
    """ASR 回环：合成的音频再识别回来，比对是否还原成目标词。

    这是唯一能自动发现"音色把 spoon 念成 us boon"这类问题的手段。
    """
    import warnings
    warnings.filterwarnings("ignore")
    from faster_whisper import WhisperModel

    ep_dir = ASSETS / ep_id
    manifest = json.loads((ep_dir / "audio.json").read_text(encoding="utf-8"))
    items = list(manifest["words"].items())
    if limit:
        items = items[:limit]

    asr = WhisperModel("base", device="cpu", compute_type="int8")
    hits, misses = 0, []
    for w, meta in items:
        p = ep_dir / meta["file"]
        if not p.exists():
            misses.append((w, "文件缺失"))
            continue
        segs, _ = asr.transcribe(str(p), language="en")
        got = "".join(s.text for s in segs).strip().lower().strip('.,!?"\' ')
        if got == w.lower():
            hits += 1
        else:
            misses.append((w, got))

    print(f"ASR 回环: {hits}/{len(items)} 还原 "
          f"({hits/len(items):.0%})" if items else "无音频")
    if misses:
        print(f"\n未还原 {len(misses)} 个（多为同音词或 ASR 自身限制）:")
        for w, got in misses[:30]:
            print(f"  {w:<16} → {got!r}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    engine = "gpt"
    if "--engine" in sys.argv:
        engine = sys.argv[sys.argv.index("--engine") + 1]
    voice = GPT_VOICE if engine == "gpt" else MM_VOICE
    if "--voice" in sys.argv:
        voice = sys.argv[sys.argv.index("--voice") + 1]

    ep_id = args[0] if args else "0101"
    force = "--force" in sys.argv
    if "--verify" in sys.argv:
        return do_verify(ep_id, limit)
    if "--lesson" in sys.argv:
        return do_lesson_audio(ep_id, engine, voice, force)
    return do_gen(ep_id, limit, engine, voice, force)


if __name__ == "__main__":
    raise SystemExit(main())
