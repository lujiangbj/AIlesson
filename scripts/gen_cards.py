#!/usr/bin/env python3
"""生产单词卡图片：筛选可配图的词 → 4 宫格批量生图 → 切成单张卡。

为什么 4 宫格：实测 9 宫格每格 402px 虽然线条不糊，但画质观感不如
4 宫格的 627px。生图请求量降到 1/4，够用了。

为什么先筛词：抽象词/集合名词配不出无歧义图（实测 furniture 被画成
一组家具，孩子只会说 chair），硬生图是浪费请求。

用法:
    # 1. 判定哪些词能配图（调 LLM，结果落盘复用）
    python3 scripts/gen_cards.py 0101 --judge

    # 2. 看筛选结果
    python3 scripts/gen_cards.py 0101 --plan

    # 3. 生图（可指定只跑前 N 组试水）
    python3 scripts/gen_cards.py 0101 --gen --limit 2
    python3 scripts/gen_cards.py 0101 --gen           # 全量
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ailesson.llm import LLMClient  # noqa: E402
from ailesson.pickable import GOOD, RISKY, judge_words  # noqa: E402
from ailesson.wordsense import collect_senses, unspoken_words  # noqa: E402

KEY = "sk-mg-cilsvjyvwgrq5m47n54fmvw4biayh6hnjsuqr5y"
API = "https://model.zhenguanyu.com/v1/images/generations"
MODEL = "openai-gpt-image-2"

VOCAB = ROOT / "data" / "friends" / "vocab"
ASSETS = ROOT / "data" / "friends" / "assets"
KNOWN_LEVEL = "A1"       # A1 算已会，A2 以上才需要教
GRID = 4                 # 2x2
INSET = 8                # 切格时向内收，去掉分隔白条

STYLE = (
    "flat vector illustration, educational flashcard style, "
    "bold black outlines of uniform weight on every subject, flat cel-shading "
    "with at most two tones per surface, warm friendly palette, "
    "soft pastel single-tone background per cell, "
    "subject centered and filling its cell, simple and instantly recognizable"
)

# 单体物件容易往写实漂（实测 spoon 被画成照片级金属渐变，描边覆盖率
# 1.2% vs 多人场景的 13~19%），必须显式堵住
NO_DRIFT = (
    "Render every cell in the SAME flat cartoon style, including cells that "
    "contain a single isolated object: no photorealistic rendering, "
    "no airbrushed or smooth metallic gradients, no glossy highlights, "
    "no 3D shading. Objects must look drawn, not photographed."
)


PARSED = ROOT / "data" / "friends" / "parsed"


def load_new_words(ep_id: str) -> list[str]:
    """取该集的生词（A2 以上），按词频降序。"""
    p = VOCAB / f"{ep_id}.json"
    if not p.exists():
        sys.exit(f"没有 {p}，先跑: python3 scripts/friends_cefr.py {ep_id} --llm --json")
    data = json.loads(p.read_text(encoding="utf-8"))
    return [
        e["token"] for e in data["entries"]
        if e.get("level") and e["level"] != KNOWN_LEVEL
        and e["category"] in ("word", "contraction")
    ]


def load_senses(ep_id: str, words: list[str]) -> tuple[list, dict, set]:
    """给生词绑定剧中原句、归并词形变体、剔掉只在舞台提示里出现的词。

    返回 (senses, word→例句, 被剔掉的未说出口的词)。
    """
    doc = json.loads((PARSED / f"{ep_id}.json").read_text(encoding="utf-8"))
    pool = set(words)
    dropped = unspoken_words(doc["items"], pool)
    senses = collect_senses(doc["items"], pool - dropped)
    examples = {s.display: s.examples for s in senses}
    return senses, examples, dropped


def judge_path(ep_id: str) -> Path:
    return ASSETS / ep_id / "pickable.json"


def do_judge(ep_id: str, force: bool = False) -> list[dict]:
    p = judge_path(ep_id)
    if p.exists() and not force:
        print(f"已有判定结果 {p.relative_to(ROOT)}，加 --force 重跑")
        return json.loads(p.read_text(encoding="utf-8"))["verdicts"]

    words = load_new_words(ep_id)
    senses, examples, dropped = load_senses(ep_id, words)

    print(f"生词 {len(words)} 个")
    print(f"  剔掉只在舞台提示里出现的 {len(dropped)} 个（剧中听不到）")
    print(f"  词形归并后 {len(senses)} 个 lemma")
    print(f"判定配图可行性（带原句绑定词义）...", flush=True)

    targets = [s.display for s in senses]
    verdicts = judge_words(
        targets, LLMClient(),
        context=f"《老友记》{ep_id} 台词", senses=examples,
    )

    by_display = {s.display: s for s in senses}
    rows = []
    for v in verdicts:
        row = v.to_dict()
        s = by_display.get(v.word)
        if s:
            row["count"] = s.count
            row["forms"] = list(s.forms)
            row["examples"] = s.examples
        rows.append(row)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "episode_id": ep_id,
        "total_words": len(words),
        "dropped_unspoken": sorted(dropped),
        "lemmas": len(senses),
        "verdicts": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    c = Counter(v.verdict for v in verdicts)
    print(f"good {c[GOOD]} / risky {c[RISKY]} / no {c['no']}")
    print(f"→ {p.relative_to(ROOT)}")
    return rows


def load_plan(ep_id: str) -> list[dict]:
    p = judge_path(ep_id)
    if not p.exists():
        sys.exit(f"先跑: python3 scripts/gen_cards.py {ep_id} --judge")
    rows = json.loads(p.read_text(encoding="utf-8"))["verdicts"]
    return [r for r in rows if r["verdict"] in (GOOD, RISKY)]


def build_prompt(batch: list[dict]) -> str:
    """拼 2x2 宫格 prompt。每格一句独立描述，格间互不干扰。

    文字策略：不再一律禁止——序数词(fifth)、符号(plus)、标牌(exit) 这类词
    离了文字就没法表意。改成"能不画就不画，该画就画"，避免模型在纯实物格里
    自作主张加装饰性乱码。
    """
    cells = "\n".join(
        f"  Cell {i+1} ({r['word']}): {r['subject']}"
        for i, r in enumerate(batch))
    return (
        f"A 2x2 grid of {len(batch)} separate illustrations for an English "
        f"vocabulary flashcard set. Divide the square canvas into exactly "
        f"2 rows and 2 columns of equal size, separated by clean thin white "
        f"gutters. Read cells left-to-right, top-to-bottom.\n"
        f"{cells}\n"
        f"Each cell illustrates ONLY its own subject, on its own FLAT plain "
        f"pastel background; adjacent cells use different background colors. "
        f"Backgrounds must stay empty — no buildings, streets, landscapes, "
        f"crowds or scenery, even if a cell's description mentions a setting; "
        f"in that case keep only the one or two props that carry the meaning. "
        f"Where a subject involves a person, draw the complete figure as "
        f"described rather than cropping to a body part.\n"
        f"{STYLE}.\n"
        f"{NO_DRIFT}\n"
        f"Text rule: do NOT add decorative text, captions, labels, titles or "
        f"watermarks. Only include letters, words or numbers when a cell's "
        f"description explicitly calls for them (e.g. a sign, a chalkboard, "
        f"a number) — in that case render them correctly spelled and legible."
    )


def generate(prompt: str, out: Path, quality: str = "medium") -> dict:
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "size": "1024x1024",
        "quality": quality, "n": 1, "output_format": "png",
    })
    t0 = time.time()
    r = subprocess.run(
        ["curl", "-sS", "--noproxy", "*", "-X", "POST", API,
         "-H", f"Authorization: Bearer {KEY}",
         "-H", "Content-Type: application/json", "--data", "@-"],
        input=body, capture_output=True, text=True, timeout=900,
    )
    elapsed = time.time() - t0
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "elapsed": elapsed,
                "error": (r.stdout or r.stderr)[:300]}
    if not data.get("data") or not data["data"][0].get("b64_json"):
        return {"ok": False, "elapsed": elapsed,
                "error": json.dumps(data, ensure_ascii=False)[:300]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
    return {"ok": True, "elapsed": elapsed, "kb": out.stat().st_size // 1024}


def split_grid(src: Path, batch: list[dict], cards_dir: Path) -> list[Path]:
    from PIL import Image
    img = Image.open(src)
    w, h = img.size
    cw, ch = w // 2, h // 2
    cards_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, r in enumerate(batch):
        row, col = divmod(i, 2)
        box = (col * cw + INSET, row * ch + INSET,
               (col + 1) * cw - INSET, (row + 1) * ch - INSET)
        p = cards_dir / f"{r['word']}.png"
        img.crop(box).save(p)
        made.append(p)
    return made


def batch_id(batch: list[dict]) -> str:
    """批次文件名由词内容决定，不用序号。

    踩坑：早先用 enumerate 序号命名，判定结果一更新（某些词从 no 变成可配图）
    分组就整体位移，新 g001 覆盖了旧 g001，而 manifest 里旧词条仍指向
    g001 —— 指向了一张内容已被换掉的图。内容哈希能保证同一组词永远同一文件。
    """
    blob = "|".join(sorted(r["word"] for r in batch))
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def do_gen(ep_id: str, limit: int | None, quality: str) -> int:
    plan = load_plan(ep_id)
    ep_dir = ASSETS / ep_id
    grids_dir, cards_dir = ep_dir / "grids", ep_dir / "cards"
    manifest_path = ep_dir / "cards.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {"episode_id": ep_id, "cards": {}})

    # 已有卡片的词跳过，只给缺的词分组——这样续跑不会重排已完成的批次
    done = {w for w, v in manifest["cards"].items()
            if (ep_dir / v["file"]).exists()}
    todo = [r for r in plan if r["word"] not in done]
    batches = [todo[i:i + GRID] for i in range(0, len(todo), GRID)]
    if limit:
        batches = batches[:limit]

    print(f"可配图 {len(plan)} 词 · 已有 {len(done)} 张 · "
          f"待生成 {len(todo)} 词 → {len(batches)} 组 (2x2, {quality})", flush=True)

    ok_n = fail_n = 0
    for bi, batch in enumerate(batches, 1):
        words = [r["word"] for r in batch]
        grid_path = grids_dir / f"g-{batch_id(batch)}.png"
        print(f"[{bi}/{len(batches)}] {' | '.join(words)}", flush=True)

        res = generate(build_prompt(batch), grid_path, quality)
        if not res["ok"]:
            print(f"    失败 {res['elapsed']:.0f}s: {res['error'][:120]}",
                  flush=True)
            fail_n += 1
            continue

        cards = split_grid(grid_path, batch, cards_dir)
        for r, p in zip(batch, cards):
            manifest["cards"][r["word"]] = {
                "file": str(p.relative_to(ep_dir)),
                "grid": str(grid_path.relative_to(ep_dir)),
                "verdict": r["verdict"],
                "subject": r["subject"],
            }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        ok_n += 1
        print(f"    {res['elapsed']:.0f}s · {res['kb']}KB · 切出 {len(cards)} 张 "
              f"· 累计 {len(manifest['cards'])}", flush=True)

    print(f"\n完成 {ok_n} 组，失败 {fail_n} 组，累计 {len(manifest['cards'])} 张卡",
          flush=True)
    print(f"→ {cards_dir.relative_to(ROOT)}/", flush=True)
    return 0 if fail_n == 0 else 1


def do_plan(ep_id: str) -> int:
    data = json.loads(judge_path(ep_id).read_text(encoding="utf-8"))
    vs = data["verdicts"]
    from collections import Counter
    c = Counter(v["verdict"] for v in vs)
    usable = [v for v in vs if v["verdict"] in (GOOD, RISKY)]

    print(f"{data['episode_id']}  生词 {data.get('total_words', '?')} 个 → "
          f"归并后 {data.get('lemmas', len(vs))} 个 lemma")
    if data.get("dropped_unspoken"):
        d = data["dropped_unspoken"]
        print(f"  已剔除只在舞台提示里出现的 {len(d)} 个: "
              f"{' '.join(d[:10])}{' ...' if len(d) > 10 else ''}")
    print(f"  good  {c[GOOD]:>4}  单图能教")
    print(f"  risky {c[RISKY]:>4}  能画但易混，subject 里给了区分线索")
    print(f"  no    {c['no']:>4}  单图教不了，走文字卡兜底")
    print(f"\n可配图 {len(usable)} 词 → {-(-len(usable)//GRID)} 次生图请求 (2x2)\n")

    print("词义绑定抽样（subject 应对应例句里的义项）:")
    for v in usable[:6]:
        ex = (v.get("examples") or [""])[0]
        print(f"  {v['word']:<12} 例句: {ex[:52]}")
        print(f"  {'':<12} 画面: {v['subject'][:52]}")
    print("\n判为 no 的词（抽样）:")
    for v in [x for x in vs if x["verdict"] == "no"][:10]:
        print(f"  {v['word']:<14} {v.get('reason') or ''}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ep_id = args[0] if args else "0101"

    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    quality = "medium"
    if "--quality" in sys.argv:
        quality = sys.argv[sys.argv.index("--quality") + 1]

    if "--judge" in sys.argv:
        do_judge(ep_id, force="--force" in sys.argv)
        return 0
    if "--plan" in sys.argv:
        return do_plan(ep_id)
    if "--gen" in sys.argv:
        return do_gen(ep_id, limit, quality)

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
