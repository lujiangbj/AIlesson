#!/usr/bin/env python3
"""给 chunk 和教学句生成图片（4 宫格批量 → 切卡）。

跟单词卡的差别：短语和句子画的是**一个情境**，不是单个物体。所以先让 LLM
把每条文本转成画面描述（它才知道 "wanna kill myself" 该画什么），再送生图。

沿用单词卡验证过的约束：2x2 宫格、纯色背景、扁平卡通、禁止文字
（图上印了英文就等于把答案写在卡面上）。

用法:
    python3 scripts/gen_lesson_images.py 0101 --describe   # LLM 出画面描述
    python3 scripts/gen_lesson_images.py 0101 --gen        # 生图
    python3 scripts/gen_lesson_images.py 0101 --gen --limit 2
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from ailesson.llm import LLMClient, LLMError  # noqa: E402
from gen_cards import NO_DRIFT, STYLE, generate, split_grid  # noqa: E402

ASSETS = ROOT / "data" / "friends" / "assets"
GRID = 4

DESCRIBE_SYSTEM = """你在为英语教学卡片设计画面。给你一批英文短语或句子，
每条要写一句英文画面描述（scene），供生图模型使用。

规则：
1. 画的是**这句话表达的情境**，不是逐词翻译。
   "wanna kill myself" → 一个人抱头崩溃、脸埋在手里（不要画自杀）
   "sounds like a date" → 两人隔桌吃饭、旁人挑眉暗示的样子
2. **背景必须纯色**，只留一两件承载语义的道具。不要街景、建筑群、人群。
3. 涉及人物就画完整的人（full figure），不要只画手或局部。
4. **绝对不要出现任何英文文字、数字、标签**。图上印了词就等于把答案
   写在卡面上，题目作废。
5. 一句话说完，60 词以内，具体到能直接喂给生图模型。
6. 若这条文本抽象到画不出可辨识情境（纯语法性的、纯语气词），
   scene 填 null。

只输出 JSON 数组：
[{"id":"kind_of","scene":"a full figure of a person shrugging with one hand raised, ambivalent expression, plain pastel background","note":null},
 {"id":"x","scene":null,"note":"纯语法结构，无情境"}]"""


def describe(ep_id: str, force: bool) -> int:
    lp = ASSETS / ep_id / "lesson.json"
    if not lp.exists():
        sys.exit(f"先跑: python3 scripts/gen_lesson.py {ep_id} --extract")
    lesson = json.loads(lp.read_text(encoding="utf-8"))
    out = ASSETS / ep_id / "lesson_scenes.json"
    if out.exists() and not force:
        print(f"已有 {out.relative_to(ROOT)}，加 --force 重跑")
        return 0

    targets = ([{"id": c["id"], "text": c["text"], "kind": "chunk"}
                for c in lesson["chunks"]]
               + [{"id": s["id"], "text": s["text"], "kind": "sentence"}
                  for s in lesson["sentences"]])
    print(f"{len(targets)} 条（{len(lesson['chunks'])} chunk + "
          f"{len(lesson['sentences'])} 句）→ LLM 出画面描述...", flush=True)

    llm = LLMClient()
    scenes: dict[str, dict] = {}
    batch = 12
    for i in range(0, len(targets), batch):
        group = targets[i:i + batch]
        payload = [{"id": g["id"], "text": g["text"]} for g in group]
        try:
            data = llm.complete_json(
                json.dumps(payload, ensure_ascii=False),
                system=DESCRIBE_SYSTEM, max_tokens=16384)
        except LLMError as e:
            print(f"  批 {i//batch+1} 失败: {e}"[:110], flush=True)
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if isinstance(row, dict) and row.get("id"):
                scenes[str(row["id"])] = row
        print(f"  {min(i+batch, len(targets))}/{len(targets)}", flush=True)

    rows = []
    for t in targets:
        r = scenes.get(t["id"], {})
        rows.append({**t,
                     "scene": (r.get("scene") or None),
                     "note": r.get("note")})

    out.write_text(json.dumps({"episode_id": ep_id, "items": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in rows if r["scene"])
    print(f"\n可配图 {n_ok}/{len(rows)} → {-(-n_ok//GRID)} 次生图请求")
    print(f"→ {out.relative_to(ROOT)}")
    return 0


def build_prompt(batch: list[dict]) -> str:
    cells = "\n".join(
        f"  Cell {i+1}: {r['scene']}" for i, r in enumerate(batch))
    return (
        f"A 2x2 grid of {len(batch)} separate illustrations for an English "
        f"learning card set. Divide the square canvas into exactly 2 rows and "
        f"2 columns of equal size, separated by clean thin white gutters. "
        f"Read cells left-to-right, top-to-bottom.\n{cells}\n"
        f"Each cell illustrates ONLY its own scene, on its own FLAT plain "
        f"pastel background; adjacent cells use different background colors. "
        f"Backgrounds must stay empty — no buildings, streets, landscapes or "
        f"crowds. Draw complete figures, not cropped body parts.\n"
        f"{STYLE}.\n{NO_DRIFT}\n"
        f"Text rule: absolutely NO text, letters, words, numbers or labels "
        f"anywhere in the image."
    )


def do_gen(ep_id: str, limit: int | None, quality: str) -> int:
    sp = ASSETS / ep_id / "lesson_scenes.json"
    if not sp.exists():
        sys.exit(f"先跑: python3 scripts/gen_lesson_images.py {ep_id} --describe")
    items = [r for r in json.loads(sp.read_text(encoding="utf-8"))["items"]
             if r.get("scene")]

    ep_dir = ASSETS / ep_id
    grids_dir = ep_dir / "lesson_grids"
    mpath = ep_dir / "lesson_images.json"
    manifest = (json.loads(mpath.read_text(encoding="utf-8"))
                if mpath.exists() else {"episode_id": ep_id, "images": {}})

    done = {k for k, v in manifest["images"].items()
            if (ep_dir / v["file"]).exists()}
    todo = [r for r in items if r["id"] not in done]
    batches = [todo[i:i + GRID] for i in range(0, len(todo), GRID)]
    if limit:
        batches = batches[:limit]

    print(f"可配图 {len(items)} · 已有 {len(done)} · 待生成 {len(todo)} "
          f"→ {len(batches)} 组", flush=True)

    ok = fail = 0
    for bi, batch in enumerate(batches, 1):
        ids = [r["id"] for r in batch]
        tag = hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:10]
        grid_path = grids_dir / f"g-{tag}.png"
        print(f"[{bi}/{len(batches)}] {' | '.join(ids)}", flush=True)

        res = generate(build_prompt(batch), grid_path, quality)
        if not res["ok"]:
            print(f"    失败 {res['elapsed']:.0f}s: {res['error'][:110]}",
                  flush=True)
            fail += 1
            continue

        # split_grid 按 word 字段命名，这里用 id
        cells = split_grid(grid_path, [{"word": r["id"]} for r in batch],
                           ep_dir / "lesson_cards")
        for r, p in zip(batch, cells):
            manifest["images"][r["id"]] = {
                "file": str(p.relative_to(ep_dir)),
                "grid": str(grid_path.relative_to(ep_dir)),
                "kind": r["kind"], "text": r["text"], "scene": r["scene"],
            }
        mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        ok += 1
        print(f"    {res['elapsed']:.0f}s · 累计 {len(manifest['images'])}",
              flush=True)

    print(f"\n完成 {ok} 组，失败 {fail}，累计 {len(manifest['images'])} 张",
          flush=True)
    return 0 if fail == 0 else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep_id = args[0] if args else "0101"
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    quality = "medium"
    if "--quality" in sys.argv:
        quality = sys.argv[sys.argv.index("--quality") + 1]

    if "--describe" in sys.argv:
        return describe(ep_id, "--force" in sys.argv)
    if "--gen" in sys.argv:
        return do_gen(ep_id, limit, quality)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
