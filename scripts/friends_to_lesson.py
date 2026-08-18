#!/usr/bin/env python3
"""把 Friends 资产打包成课程引擎能读的 lesson JSON。

产出 data/friends/lessons/lesson-friends-<ep>.json，结构对齐 MVP，
让 episode.py / lesson3.py 零改动就能跑。

用法:
    python3 scripts/friends_to_lesson.py 0101
    python3 scripts/friends_to_lesson.py 0101 --check   # 只看覆盖报告
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.friends_lesson import build_lesson, coverage_report  # noqa: E402

ASSETS = ROOT / "data" / "friends" / "assets"
VOCAB = ROOT / "data" / "friends" / "vocab"
OUT_DIR = ROOT / "data" / "friends" / "lessons"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep = args[0] if args else "0101"
    ep_dir = ASSETS / ep
    if not (ep_dir / "cards.json").exists():
        sys.exit(f"没有 {ep_dir}/cards.json，先跑生产流程")

    parsed = json.loads(
        (ROOT / "data" / "friends" / "parsed" / f"{ep}.json").read_text())
    lesson = build_lesson(
        ep_dir,
        vocab_path=VOCAB / f"{ep}.json",
        title=f"Friends S{parsed['season']}E{parsed['episode']} "
              f"— {parsed['title'][:40]}",
    )
    r = coverage_report(lesson)

    print(f"{lesson['id']}  {lesson['title']}")
    print(f"  词      {r['words']:>4}  （图+音都齐的）")
    print(f"  chunk   {r['chunks']:>4}  其中 {r['chunks_with_image']} 张有图")
    print(f"  句子    {r['sentences']:>4}  其中 {r['sentences_with_image']} 张有图")
    print(f"  被句子覆盖的词 {r['words_in_sentences']}/{r['words']}")
    if r["orphan_words"]:
        n = len(r["orphan_words"])
        print(f"  孤儿词 {n} 个（FR-3.4 的顺带词）: "
              f"{' '.join(r['orphan_words'][:10])}{' ...' if n > 10 else ''}")

    # PRD FR-3.1/3.3：每节 6 个重点词、至少 2 chunk + 2 句
    n_lesson = max(1, r["words_in_sentences"] // 6)
    print(f"\n按每节 6 个重点词估：约 {n_lesson} 节课")
    if r["chunks"] < 2 or r["sentences"] < 2:
        print("  ⚠ chunk 或句子不足 2 个，环节 6/7/9/10 会空转")

    if "--check" in sys.argv:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"lesson-{lesson['id']}.json"
    out.write_text(json.dumps(lesson, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n→ {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
