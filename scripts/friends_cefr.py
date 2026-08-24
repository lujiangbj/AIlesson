#!/usr/bin/env python3
"""给一集剧本的词汇按词频 + CEFR 等级分层，输出选材用的分级表。

两层处理（见 src/ailesson/content/vocab_cefr.py）：
1. 规则：词形还原后直查 CEFR-J 词表
2. LLM：把查不到的送模型归一化（goodnight→good night、kinda→kind of），
   再回查词表——词表等级优先，查不到才用 LLM 估级

词表: data/cefr/  (CEFR-J A1-B2 + Octanove C1-C2, openlanguageprofiles)

用法:
    python3 scripts/friends_cefr.py 0101              # 只跑规则，不花钱
    python3 scripts/friends_cefr.py 0101 --llm        # 加 LLM 归一化（推荐）
    python3 scripts/friends_cefr.py 0101 --llm --json # 结果落盘
    python3 scripts/friends_cefr.py 0101 --list A2    # 列出某级别全部词
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.course.cache import LLMCache  # noqa: E402
from ailesson.infra.llm import LLMClient  # noqa: E402
from ailesson.content.vocab_cefr import (  # noqa: E402
    LEVELS,
    VocabProfile,
    build_profile,
    load_wordlist,
)

PARSED = ROOT / "data" / "friends" / "parsed"
CEFR_DIR = ROOT / "data" / "cefr"
OUT_DIR = ROOT / "data" / "friends" / "vocab"

CAT_LABEL = {
    "word": "实词", "contraction": "口语缩略", "proper_noun": "专有名词",
    "interjection": "语气词", "fragment": "解析碎片",
}


def load_episode(ep_id: str) -> dict:
    src = PARSED / f"{ep_id}.json"
    if not src.exists():
        sys.exit(f"没有 {src}，先跑: python3 scripts/friends_parse.py {ep_id}")
    return json.loads(src.read_text(encoding="utf-8"))


def report(doc: dict, p: VocabProfile) -> None:
    print(f"{p.episode_id}  S{doc['season']}E{doc['episode']}  {doc['title'][:50]}")
    print(f"{p.tokens} 词次 / {p.types} 独立词\n")

    by_level = p.by_level()
    print(f"{'级别':<6}{'词数':>6}{'占比':>8}{'词次':>7}{'词次占比':>9}   典型词")
    print("-" * 76)
    for lv in LEVELS:
        items = by_level.get(lv)
        if not items:
            continue
        n_tok = sum(e.count for e in items)
        top = " ".join(e.lemma or e.token for e in items[:6])
        print(f"{lv:<6}{len(items):>6}{len(items)/p.types:>7.1%}"
              f"{n_tok:>7}{n_tok/p.tokens:>8.1%}   {top}")

    graded = sum(len(v) for v in by_level.values())
    print("-" * 76)
    print(f"已定级 {graded} 词（占 {graded/p.types:.0%}）\n")

    print("不进教学池的部分：")
    for cat, items in sorted(p.by_category().items(),
                             key=lambda kv: -len(kv[1])):
        if cat in ("word", "contraction"):
            continue
        top = " ".join(e.token for e in items[:8])
        print(f"  {CAT_LABEL.get(cat, cat):<10}{len(items):>4} 个   {top}")

    if p.unresolved:
        print(f"\n仍未定级的实词 {len(p.unresolved)} 个（词表和 LLM 都没给等级）：")
        print("  " + " ".join(e.token for e in p.unresolved[:25]))

    fixed = [e for e in p.entries if e.source == "llm" and e.level]
    if fixed:
        hit = [e for e in fixed if e.note and "命中词表" in e.note]
        print(f"\nLLM 归一化救回 {len(fixed)} 词，其中 {len(hit)} 个回查命中词表：")
        for e in hit[:10]:
            print(f"  {e.token:<14}→ {e.lemma:<18}{e.level}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep_id = args[0] if args else "0101"
    doc = load_episode(ep_id)
    texts = [i["text"] for i in doc["items"] if i["type"] == "line"]

    wordlist = load_wordlist(CEFR_DIR)
    llm = None
    if "--llm" in sys.argv:
        llm = LLMClient()

    cache_path = OUT_DIR / f"{ep_id}.json"
    p = build_profile(
        ep_id, texts, wordlist, llm,
        context=f"《老友记》S{doc['season']}E{doc['episode']} 台词",
    )

    if "--list" in sys.argv:
        i = sys.argv.index("--list")
        want = sys.argv[i + 1].upper() if i + 1 < len(sys.argv) else "A1"
        items = p.by_level().get(want, [])
        print(f"{want} 共 {len(items)} 词（按出现次数降序）:\n")
        for e in items:
            lemma = f"  → {e.lemma}" if e.lemma and e.lemma != e.token else ""
            print(f"  {e.count:>3}  {e.token}{lemma}")
        return 0

    report(doc, p)

    if "--json" in sys.argv:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(p.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {cache_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
