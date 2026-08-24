#!/usr/bin/env python3
"""把一集剧本切成学习段，看每段规模是否适合一节课。

切点只落在换场（Scene:）边界，不会断在场景中途。份数默认在 4~6 里
搜最均匀的方案；逐字稿没时间轴，用词数近似时长。

用法:
    python3 scripts/friends_segment.py 0101            # 自动选份数
    python3 scripts/friends_segment.py 0101 -n 5       # 固定 5 段
    python3 scripts/friends_segment.py 0101 --vocab    # 附各段生词量（需已跑 friends_cefr）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.content.segment import segment_episode, spread  # noqa: E402
from ailesson.content.vocab_cefr import token_freq  # noqa: E402

PARSED = ROOT / "data" / "friends" / "parsed"
VOCAB = ROOT / "data" / "friends" / "vocab"

# 假设：A1 算已会，A2 以上算生词。真实水平应由 PRD FR-1 分诊决定
KNOWN_LEVEL = "A1"
RUNTIME_MIN = 24          # 一集约 24 分钟，用于估算每段时长


def load_levels(ep_id: str) -> dict[str, str] | None:
    p = VOCAB / f"{ep_id}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return {
        e["token"]: e["level"]
        for e in data["entries"]
        if e.get("level") and e["category"] in ("word", "contraction")
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    ep_id = args[0] if args else "0101"

    n = None
    if "-n" in sys.argv:
        n = int(sys.argv[sys.argv.index("-n") + 1])

    src = PARSED / f"{ep_id}.json"
    if not src.exists():
        sys.exit(f"没有 {src}，先跑 scripts/friends_parse.py {ep_id}")
    doc = json.loads(src.read_text(encoding="utf-8"))

    segs = segment_episode(doc["items"], n)
    total_words = sum(s.words for s in segs)
    levels = load_levels(ep_id) if "--vocab" in sys.argv else None

    print(f"{ep_id}  S{doc['season']}E{doc['episode']}  {doc['title'][:46]}")
    print(f"{doc['stats']['lines']} 句 / {total_words} 词 / "
          f"{doc['stats']['scenes']} 换场 → 切成 {len(segs)} 段"
          f"（不均衡度 {spread(segs):.2f}）\n")

    head = f"{'段':>3}{'场':>4}{'句':>5}{'词':>6}{'约时长':>8}"
    if levels:
        head += f"{'生词':>6}{'≈节课':>7}"
    print(head + "   地点")
    print("-" * (78 if levels else 66))

    for s in segs:
        mins = s.words / total_words * RUNTIME_MIN
        row = (f"{s.index:>3}{len(s.chunks):>4}{len(s.lines):>5}"
               f"{s.words:>6}{mins:>7.1f}'")
        if levels:
            new = {w for w in token_freq(s.texts)
                   if levels.get(w) and levels[w] != KNOWN_LEVEL}
            row += f"{len(new):>6}{len(new)/6:>7.1f}"
        print(row + "   " + " · ".join(s.locations[:3]))

    print("-" * (78 if levels else 66))
    if levels:
        print(f"生词口径：{KNOWN_LEVEL} 算已会，A2 以上算生词；"
              f"节课数按 PRD 每节 6 个重点词估")
    else:
        print("加 --vocab 看各段生词量（需先跑 scripts/friends_cefr.py "
              f"{ep_id} --llm --json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
