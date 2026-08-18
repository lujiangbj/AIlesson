#!/usr/bin/env python3
"""为解析好的《老友记》剧本生成索引，供课程引擎选材用。

输出 data/friends/index.json：每集一条，含句数/说话人/词数，
以及按"适合教学"排序需要的基础统计（平均句长、独立词数）。

用法: python3 scripts/friends_index.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARSED = ROOT / "data" / "friends" / "parsed"
OUT = ROOT / "data" / "friends" / "index.json"

WORD_RE = re.compile(r"[a-zA-Z']+")
SIX = ("Monica", "Rachel", "Ross", "Chandler", "Joey", "Phoebe")


def summarize(doc: dict) -> dict:
    lines = [i for i in doc["items"] if i["type"] == "line"]
    words: list[str] = []
    by_speaker: Counter[str] = Counter()
    for ln in lines:
        w = WORD_RE.findall(ln["text"].lower())
        words += w
        by_speaker[ln["speaker"]] += 1

    return {
        "id": doc["id"],
        "season": doc["season"],
        "episode": doc["episode"],
        "special": doc.get("special"),
        "title": doc["title"],
        "lines": len(lines),
        "scenes": doc["stats"]["scenes"],
        "words": len(words),
        "unique_words": len(set(words)),
        "avg_words_per_line": round(len(words) / len(lines), 1) if lines else 0,
        "six_share": round(
            sum(by_speaker[s] for s in SIX) / len(lines), 3) if lines else 0,
        "top_speakers": by_speaker.most_common(6),
    }


def main() -> int:
    files = sorted(PARSED.glob("*.json"))
    if not files:
        print(f"没有解析结果，先跑: python3 scripts/friends_parse.py")
        return 1

    eps = [summarize(json.loads(f.read_text(encoding="utf-8"))) for f in files]
    regular = [e for e in eps if not e["special"]]
    vocab: Counter[str] = Counter()
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for i in doc["items"]:
            if i["type"] == "line":
                vocab.update(WORD_RE.findall(i["text"].lower()))

    index = {
        "source": "fangj/friends (fan transcript, script-o-rama lineage)",
        "totals": {
            "files": len(eps),
            "regular_episodes": len(regular),
            "specials": [e["id"] for e in eps if e["special"]],
            "lines": sum(e["lines"] for e in eps),
            "words": sum(e["words"] for e in eps),
            "unique_words": len(vocab),
        },
        "top_vocab": vocab.most_common(50),
        "episodes": eps,
    }
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    t = index["totals"]
    print(f"{t['files']} 个文件（{t['regular_episodes']} 常规集 + "
          f"{len(t['specials'])} 特辑）")
    print(f"{t['lines']} 句台词 / {t['words']} 词 / {t['unique_words']} 独立词")
    print(f"→ {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
