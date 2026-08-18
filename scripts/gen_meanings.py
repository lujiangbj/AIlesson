#!/usr/bin/env python3
"""给单词补中文释义（按剧中语境，不是词典义）。

为什么必须有：meaning_zh 空着会连环坏三处——
1. 答题反馈显示不出词义，学习者不知道自己蒙对还是真会
2. AI 老师讲解拿不到语义，只能讲发音（"这个词读 xxx"），毫无教学价值
3. 「看图选音」题干显示空白

释义要贴剧中用法：pot 在剧里是咖啡壶不是汤锅，wooden 是"木偶的"不是"木制的"。
所以带例句进 prompt，跟 pickable 的做法一致。

用法:
    python3 scripts/gen_meanings.py 0101            # 生成并写回 lesson JSON
    python3 scripts/gen_meanings.py 0101 --dry      # 只看结果不写
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.llm import LLMClient, LLMError  # noqa: E402

ASSETS = ROOT / "data" / "friends" / "assets"
LESSONS = ROOT / "data" / "friends" / "lessons"

SYSTEM = """你在给英语学习卡片写中文释义。学习者是成人，素材是美剧台词。

每个词会附上它在剧中的原句。释义要求：
1. **贴原句里的实际用法**，不是词典首义。
   pot 在 "a pot of coffee" 里是"一壶（咖啡）"，不是"锅"；
   wooden 在 "a wooden boy" 里是"木偶的"，不是"木制的"。
2. 简短，2~8 个汉字。是卡片上的一行小字，不是词典条目。
3. 动词给动词义（"抓住"而非"抓"），形容词给形容词义。
4. 口语词标出语体：kinda → "有点（口语）"。
5. 专有名词直接音译或写明是什么：Aruba → "阿鲁巴（地名）"。
6. 粗俗词照实翻译，学习者是成人，不要回避或美化。

只输出 JSON 数组，不要解释：
[{"word":"spoon","zh":"勺子"},{"word":"kinda","zh":"有点（口语）"}]"""


def load_examples(ep: str) -> dict[str, list[str]]:
    p = ASSETS / ep / "pickable.json"
    if not p.exists():
        return {}
    return {v["word"]: (v.get("examples") or [])
            for v in json.loads(p.read_text(encoding="utf-8"))["verdicts"]}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep = args[0] if args else "0101"
    lp = LESSONS / f"lesson-friends-{ep}.json"
    if not lp.exists():
        sys.exit(f"没有 {lp}，先跑 scripts/friends_to_lesson.py {ep}")

    lesson = json.loads(lp.read_text(encoding="utf-8"))
    examples = load_examples(ep)
    todo = [w for w in lesson["words"] if not w.get("meaning_zh")]
    print(f"{len(lesson['words'])} 个词，待补释义 {len(todo)} 个", flush=True)
    if not todo:
        return 0

    llm = LLMClient()
    got: dict[str, str] = {}
    batch = 25
    for i in range(0, len(todo), batch):
        group = todo[i:i + batch]
        payload = [{"word": w["lemma"],
                    "lines": examples.get(w["lemma"], [])[:1]}
                   for w in group]
        try:
            data = llm.complete_json(
                json.dumps(payload, ensure_ascii=False),
                system=SYSTEM, max_tokens=8192)
        except LLMError as e:
            print(f"  批 {i//batch+1} 失败: {e}"[:110], flush=True)
            continue
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("word") and row.get("zh"):
                    got[str(row["word"]).lower()] = str(row["zh"]).strip()
        print(f"  {min(i+batch, len(todo))}/{len(todo)}", flush=True)

    miss = [w["lemma"] for w in todo if w["lemma"] not in got]
    print(f"\n拿到 {len(got)} 条释义，缺 {len(miss)} 个"
          + (f": {' '.join(miss[:10])}" if miss else ""))

    for w in lesson["words"]:
        if w["lemma"] in got:
            w["meaning_zh"] = got[w["lemma"]]

    print("\n抽样:")
    for w in lesson["words"][:12]:
        print(f"  {w['lemma']:<14} {w.get('meaning_zh', '')}")

    if "--dry" in sys.argv:
        print("\n(--dry，未写回)")
        return 0
    lp.write_text(json.dumps(lesson, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    print(f"\n→ {lp.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
