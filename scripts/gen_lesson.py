#!/usr/bin/env python3
"""抽教学短语（chunk）和教学句（sentence），产出对齐 MVP lesson JSON 的结构。

为什么需要：PRD FR-3.2「打包单位是句子，不是词」。环节 6/7 要 chunk
听辨/跟读，环节 9/10 要句子原声/跟读。只做词那层，L2/L3 环节会空转。

句子一字不改沿用剧中原话——环节 9 要放原片音轨。

用法:
    python3 scripts/gen_lesson.py 0101 --extract        # 抽 chunk + 句子
    python3 scripts/gen_lesson.py 0101 --plan           # 看抽取结果
    python3 scripts/gen_lesson.py 0101 --extract --limit 20
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.chunker import candidate_lines, extract  # noqa: E402
from ailesson.llm import LLMClient  # noqa: E402

PARSED = ROOT / "data" / "friends" / "parsed"
ASSETS = ROOT / "data" / "friends" / "assets"
KNOWN_LEVEL = "A1"


def target_words(ep_id: str) -> set[str]:
    """生词集（A2 以上）。句子必须至少含一个，否则没教学价值。"""
    p = ROOT / "data" / "friends" / "vocab" / f"{ep_id}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return {
        e["token"] for e in data["entries"]
        if e.get("level") and e["level"] != KNOWN_LEVEL
        and e["category"] in ("word", "contraction")
    }


def do_extract(ep_id: str, limit: int | None) -> int:
    doc = json.loads((PARSED / f"{ep_id}.json").read_text(encoding="utf-8"))
    words = target_words(ep_id)
    cands = candidate_lines(doc["items"], words)
    if limit:
        cands = cands[:limit]

    print(f"{doc['stats']['lines']} 句台词 → {len(cands)} 句符合教学句窗口"
          f"（4~14 词、含生词）", flush=True)
    print("抽取 chunk 并翻译...", flush=True)

    sents, chunks = extract(
        cands, LLMClient(), context=f"《老友记》S{doc['season']}E{doc['episode']}")

    out = ASSETS / ep_id / "lesson.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "episode_id": ep_id,
        "season": doc["season"],
        "episode": doc["episode"],
        "title": doc["title"],
        "sentences": [s.to_dict() for s in sents],
        "chunks": [c.to_dict() for c in chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    cover = sum(1 for s in sents if s.chunks)
    print(f"\n{len(sents)} 句 / {len(chunks)} 个 chunk"
          f"（{cover} 句有 chunk 覆盖）")
    print(f"→ {out.relative_to(ROOT)}")
    return 0


def do_plan(ep_id: str) -> int:
    p = ASSETS / ep_id / "lesson.json"
    if not p.exists():
        sys.exit(f"先跑: python3 scripts/gen_lesson.py {ep_id} --extract")
    d = json.loads(p.read_text(encoding="utf-8"))
    sents, chunks = d["sentences"], d["chunks"]

    print(f"{d['episode_id']} S{d['season']}E{d['episode']}")
    print(f"{len(sents)} 句 / {len(chunks)} 个 chunk\n")

    from collections import Counter
    used = Counter(c for s in sents for c in s["chunks"])
    print("最常复用的 chunk:")
    by_id = {c["id"]: c for c in chunks}
    for cid, n in used.most_common(10):
        c = by_id.get(cid, {})
        print(f"  {n}× {c.get('text', cid):<24} {c.get('meaning_zh', '')}")

    print("\n教学句抽样:")
    for s in sents[:8]:
        print(f"  [{s['id']}] {s['speaker']}: {s['text'][:56]}")
        print(f"        {s['meaning_zh'][:48]}")
        if s["chunks"]:
            print(f"        chunk: {' / '.join(s['chunks'][:3])}")

    no_chunk = [s["id"] for s in sents if not s["chunks"]]
    if no_chunk:
        print(f"\n没抽出 chunk 的句子 {len(no_chunk)} 句: "
              f"{' '.join(no_chunk[:12])}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep_id = args[0] if args else "0101"
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if "--extract" in sys.argv:
        return do_extract(ep_id, limit)
    if "--plan" in sys.argv:
        return do_plan(ep_id)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
