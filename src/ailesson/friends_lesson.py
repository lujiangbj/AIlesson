"""把 Friends 资产转成课程引擎认的 lesson JSON。

课程引擎（episode.py）读的是 illit-english-mvp 那套 schema。Friends 这条线
产出的是另一套结构（pickable.json / cards.json / audio.json / lesson.json /
lesson_images.json），需要一层转换才能喂进去。

NFR-4 说数据层零改动，所以这里不改 episode.py，只做格式适配。

关键取舍：
- 只收**图和音频都齐**的词。缺图的词过不了环节 3/4（听音选图、看图选音），
  缺音的连听都听不了。宁可少而完整。
- distractors 按 CEFR 同级 + 首字母不同来挑，避免"听音选图"里四个选项
  长得太像或差得太远。
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

# 一个词至少要有这些资产才算可教
DISTRACTOR_N = 3


def _rel(ep_dir_name: str, p: str) -> str:
    """资产路径转成挂载后的 URL 相对路径。

    server.py 的 url() 直接把路径挂根上，所以这里给出
    friends/<ep>/... 形式，配合 server 挂载 data/friends/assets。
    """
    return f"friends/{ep_dir_name}/{p}"


def build_lesson(
    ep_dir: Path,
    *,
    vocab_path: Path,
    episode_id: str | None = None,
    title: str = "",
    seed: int = 20260814,
) -> dict[str, Any]:
    """产出 lesson JSON（结构对齐 MVP，路径指向 Friends 资产）。"""
    name = ep_dir.name
    episode_id = episode_id or f"friends-{name}"

    cards = json.loads((ep_dir / "cards.json").read_text())["cards"]
    audio = json.loads((ep_dir / "audio.json").read_text())
    lesson = json.loads((ep_dir / "lesson.json").read_text())
    images = {}
    p_img = ep_dir / "lesson_images.json"
    if p_img.exists():
        images = json.loads(p_img.read_text())["images"]

    # 词频和等级从 vocab 拿
    vocab = json.loads(vocab_path.read_text())
    freq = {e["token"]: e.get("count", 0) for e in vocab["entries"]}
    level = {e["token"]: e.get("level") for e in vocab["entries"]}

    # ---- words：图 + 音都齐才收 ----
    words: list[dict] = []
    for token, card in cards.items():
        au = audio.get("words", {}).get(token)
        if not au:
            continue
        words.append({
            "lemma": token,
            "freq": freq.get(token, 0),
            "audio": _rel(name, au["file"]),
            # 没单独生成慢速版，复用常速（跟读环节仍可用）
            "audio_slow": _rel(name, au["file"]),
            "image": _rel(name, card["file"]),
            "skip_image": False,
            "meaning_zh": "",          # 单词卡这轮没生成中文释义
            "cefr": level.get(token) or "",
        })
    words.sort(key=lambda w: -w["freq"])
    lemmas = {w["lemma"] for w in words}

    # ---- chunks ----
    chunks: list[dict] = []
    for c in lesson["chunks"]:
        au = audio.get("chunks", {}).get(c["id"])
        if not au or not au.get("audio_tts"):
            continue
        img = images.get(c["id"])
        chunks.append({
            "id": c["id"],
            "text": c["text"],
            "meaning_zh": c.get("meaning_zh", ""),
            "audio_tts": _rel(name, au["audio_tts"]),
            "audio_tts_slow": _rel(name, au.get("audio_tts_slow")
                                   or au["audio_tts"]),
            "image": _rel(name, img["file"]) if img else "",
            # 只保留在词表里的词，否则覆盖关系算不出来
            "covers_words": [w for w in c.get("covers_words", [])
                             if w in lemmas],
        })
    chunk_ids = {c["id"] for c in chunks}

    # ---- sentences ----
    sentences: list[dict] = []
    for s in lesson["sentences"]:
        au = audio.get("lesson_sentences", {}).get(s["id"])
        if not au or not au.get("audio_tts"):
            continue
        img = images.get(s["id"])
        kw = [w for w in s.get("key_words", []) if w in lemmas]
        cids = [c for c in s.get("chunks", []) if c in chunk_ids]
        if not kw and not cids:
            continue                   # 既不教词也没语块，环节里用不上
        sentences.append({
            "id": s["id"],
            "text_admin_only": s["text"],
            "meaning_zh": s.get("meaning_zh", ""),
            "speaker": s.get("speaker") or "",
            "audio_tts": _rel(name, au["audio_tts"]),
            "audio_tts_slow": _rel(name, au.get("audio_tts_slow")
                                   or au["audio_tts"]),
            # 没有原片音轨，用 TTS 顶上（环节 9 的"原声"降级为合成音）
            "audio_clip": _rel(name, au["audio_tts"]),
            "image": _rel(name, img["file"]) if img else "",
            "chunks": cids,
            "key_words": kw,
        })

    # ---- distractors：同 CEFR 等级优先，首字母不同，避免混淆 ----
    rng = random.Random(seed)
    pool = [w["lemma"] for w in words]
    distractors: dict[str, list[str]] = {}
    for w in words:
        me, my_lv = w["lemma"], w.get("cefr")
        same = [x for x in pool
                if x != me and level.get(x) == my_lv and x[0] != me[0]]
        other = [x for x in pool if x != me and x[0] != me[0]]
        picks = rng.sample(same, min(DISTRACTOR_N, len(same)))
        if len(picks) < DISTRACTOR_N:
            rest = [x for x in other if x not in picks]
            picks += rng.sample(rest, min(DISTRACTOR_N - len(picks), len(rest)))
        distractors[me] = picks

    return {
        "id": episode_id,
        "title": title or lesson.get("title", ""),
        "level": 2,                    # Friends 比 peppa 难，标 2
        "duration_seconds": 24 * 60,
        "words": words,
        "episode_words": [w["lemma"] for w in words],
        "new_words": [w["lemma"] for w in words],
        "reused_words": [],
        "chunks": chunks,
        "sentences": sentences,
        "distractors": distractors,
    }


def coverage_report(lesson: dict) -> dict[str, Any]:
    """算一下这份 lesson 够不够跑完 14 环节。

    PRD FR-3.3 要求每节至少 2 个可用 chunk、2 个可用句子，
    所以词必须被句子覆盖到，否则 L2/L3 环节空转。
    """
    lemmas = {w["lemma"] for w in lesson["words"]}
    by_chunk = {c["id"]: set(c["covers_words"]) for c in lesson["chunks"]}
    covered: set[str] = set()
    for s in lesson["sentences"]:
        covered |= set(s["key_words"])
        for cid in s["chunks"]:
            covered |= by_chunk.get(cid, set())
    covered &= lemmas

    return {
        "words": len(lemmas),
        "chunks": len(lesson["chunks"]),
        "sentences": len(lesson["sentences"]),
        "words_in_sentences": len(covered),
        "orphan_words": sorted(lemmas - covered),
        "chunks_with_image": sum(1 for c in lesson["chunks"] if c["image"]),
        "sentences_with_image": sum(1 for s in lesson["sentences"] if s["image"]),
    }
