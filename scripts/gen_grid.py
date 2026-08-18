#!/usr/bin/env python3
"""GPT Image 生成 N 宫格教学图，并切成单词卡。

为什么用宫格：一节课 6 个重点词 × N 节，逐词生图请求量太大。一次出 4/9 格
能把请求数压到 1/4~1/9。前提是格子能干净切开——「听音选图」环节要的是
单张图，不是宫格。

用法:
    python3 scripts/gen_grid.py wedding wine spoon stairs        # 4 宫格
    python3 scripts/gen_grid.py w1 w2 ... w9 --quality high      # 9 宫格
    python3 scripts/gen_grid.py --words-file list.txt --no-split
"""
from __future__ import annotations

import base64
import json
import math
import subprocess
import sys
import time
from pathlib import Path

KEY = "sk-mg-cilsvjyvwgrq5m47n54fmvw4biayh6hnjsuqr5y"
URL = "https://model.zhenguanyu.com/v1/images/generations"
MODEL = "openai-gpt-image-2"

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "friends" / "assets"

STYLE = (
    "flat vector illustration, children's educational flashcard style, "
    "bold clean outlines, warm friendly palette, soft pastel background per cell, "
    "each object centered and filling its cell, simple and instantly recognizable"
)


def build_prompt(words: list[str], cols: int, rows: int) -> str:
    cells = "; ".join(f"cell {i+1}: {w}" for i, w in enumerate(words))
    return (
        f"A {rows}x{cols} grid of {len(words)} separate illustrations for an "
        f"English vocabulary flashcard set. "
        f"Divide the square canvas into exactly {rows} rows and {cols} columns "
        f"of equal size, separated by clean thin white gutters. "
        f"Read cells left-to-right, top-to-bottom. {cells}. "
        f"Each cell contains exactly ONE object on its own plain background. "
        f"{STYLE}. "
        f"CRITICAL: absolutely NO text, NO letters, NO words, NO labels, "
        f"NO numbers anywhere in the image. Objects only."
    )


def generate(prompt: str, quality: str, out: Path) -> dict:
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "size": "1024x1024",
        "quality": quality, "n": 1, "output_format": "png",
    })
    t0 = time.time()
    r = subprocess.run(
        ["curl", "-sS", "--noproxy", "*", "-X", "POST", URL,
         "-H", f"Authorization: Bearer {KEY}",
         "-H", "Content-Type: application/json", "--data", "@-"],
        input=body, capture_output=True, text=True, timeout=900,
    )
    elapsed = time.time() - t0

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": r.stdout[:300] or r.stderr[:300],
                "elapsed": elapsed}

    if not data.get("data") or not data["data"][0].get("b64_json"):
        return {"ok": False, "error": json.dumps(data, ensure_ascii=False)[:400],
                "elapsed": elapsed}

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
    return {"ok": True, "elapsed": elapsed, "path": out,
            "kb": out.stat().st_size // 1024,
            "usage": data.get("usage")}


def split_grid(
    src: Path, words: list[str], cols: int, rows: int, inset: int = 8
) -> list[Path]:
    """按等分切格。

    inset 向内收缩若干像素，去掉分隔白条——等分切出来的格子会在朝向
    画面内侧的两条边上留 4~5px 白边（实测 2x2 的分隔条宽约 8px），
    不修的话卡片有条不对称的 L 形白框。
    """
    from PIL import Image

    img = Image.open(src)
    w, h = img.size
    cw, ch = w // cols, h // rows
    made = []
    for i, word in enumerate(words):
        r, c = divmod(i, cols)
        box = (c * cw + inset, r * ch + inset,
               (c + 1) * cw - inset, (r + 1) * ch - inset)
        p = src.parent / f"{src.stem}-{i+1}-{word}.png"
        img.crop(box).save(p)
        made.append(p)
    return made


def main() -> int:
    argv = sys.argv[1:]
    quality = "medium"
    if "--quality" in argv:
        i = argv.index("--quality")
        quality = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    no_split = "--no-split" in argv
    argv = [a for a in argv if not a.startswith("--")]

    words = argv
    if not words:
        print(__doc__)
        return 1

    n = len(words)
    cols = rows = int(math.isqrt(n))
    if cols * rows != n:
        print(f"词数 {n} 不是完全平方数，请给 4 或 9 个词")
        return 1

    tag = f"grid{n}-{quality}"
    out = OUT / "test" / f"{tag}.png"
    prompt = build_prompt(words, cols, rows)

    print(f"{rows}x{cols} 宫格 · quality={quality} · {n} 词")
    print(f"词序: {' | '.join(words)}")
    print("生成中（high 画质可能几分钟）...")

    res = generate(prompt, quality, out)
    if not res["ok"]:
        print(f"失败 ({res['elapsed']:.0f}s): {res['error']}")
        return 1

    print(f"完成 {res['elapsed']:.0f}s · {res['kb']}KB · {out.relative_to(ROOT)}")
    if res.get("usage"):
        print(f"usage: {res['usage']}")

    if not no_split:
        cells = split_grid(out, words, cols, rows)
        print(f"切出 {len(cells)} 张单词卡:")
        for p in cells:
            print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
