#!/usr/bin/env python3
"""《老友记》剧本 HTML → 结构化 JSON（说话人 / 台词 / 场景）。

数据源: data/friends/github (fangj/friends, 粉丝逐字稿, 全 10 季)
输出:   data/friends/parsed/SSEE.json

用法:
    python3 scripts/friends_parse.py              # 转全部 229 个文件
    python3 scripts/friends_parse.py 0101         # 只转 S1E1
    python3 scripts/friends_parse.py 0101 --text  # 同时输出可读 txt
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "data" / "friends" / "github" / "season"
OUT_DIR = ROOT / "data" / "friends" / "parsed"

# 逐字稿抬头的元信息行，不算台词
SKIP_PREFIXES = (
    "Written by", "Transcribed by", "Additional transcribing",
    "Teleplay by", "Story by", "Transcriber", "Note:", "Originally written by",
)

# 说话人行: "Monica:" / "Mrs. Geller:" / "Ross: (缩着)" / "All:"
SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z.'\- ]{0,24}?)\s*(\([^)]*\))?\s*:\s*(.*)$")
# 场景/舞台说明: [Scene: ...] / 整行括号 / 收尾括号写错的 [Scene: ...)
STAGE_RE = re.compile(r"^(?:\[\s*Scene\b.*|[\[(].*[\])]\s*)$", re.IGNORECASE)

# 六位主角 + 常见配角的规范拼写。不同季转写者风格不一
# （MONICA / Monica / MOnica），统一成首字母大写形式便于按角色检索。
CANON = {n.lower(): n for n in (
    "Monica", "Rachel", "Ross", "Chandler", "Joey", "Phoebe",
    "Gunther", "Janice", "Richard", "Emily", "Carol", "Susan", "Mike",
    "Paul", "Ursula", "Estelle", "Frank", "Charlie", "Amy", "All",
)}


def canon_speaker(name: str) -> str:
    """归一化说话人名：已知角色套规范拼写，其余保持原样只压全大写。"""
    key = name.strip().rstrip(".").lower()
    if key in CANON:
        return CANON[key]
    n = name.strip().rstrip(".")
    return n.title() if n.isupper() and len(n) > 2 else n


def to_lines(raw: str) -> list[str]:
    """HTML → 段落列表，保留 <p>/<br> 造成的换行。

    原稿里 [Scene: ...] 常被 <br> 拆成多行，这里先按括号配平合回一行，
    否则场景描述会被误判成台词。
    """
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    s = re.sub(r"(?i)<\s*(br|/p|/div|/tr|/h\d|/li)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")

    out: list[str] = []
    for line in s.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        # 上一行方括号没闭合 → 当作它的续行接上。
        # 但原稿里有 "[Scene: ... enters.)" 这种用圆括号收尾的手误，
        # 若不设边界会把后面整集台词都吞进同一行，所以：
        # 遇到疑似说话人行就停，且单条场景描述不超过 400 字。
        if (out
                and out[-1].startswith("[")
                and out[-1].count("[") > out[-1].count("]")
                and len(out[-1]) < 400
                and not SPEAKER_RE.match(line)):
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def parse(path: Path) -> dict:
    lines = to_lines(path.read_text(encoding="utf-8", errors="ignore"))
    title = lines[0] if lines else path.stem
    stem = path.stem

    # "0101" → S1E1；"1017-1018" → S10E17-18；
    # "0423uncut"/"07outtakes" 是特辑，只解出季号，episode 记为后缀
    m = re.match(r"^(\d{2})(\d{2})(?:-\d{2}(\d{2}))?([a-z]+)?$", stem)
    season, episode, special = None, None, None
    if m:
        season = int(m.group(1))
        episode = m.group(2).lstrip("0")
        if m.group(3):
            episode = f"{episode}-{m.group(3).lstrip('0')}"
        special = m.group(4)
    else:
        m2 = re.match(r"^(\d{2})([a-z]+)$", stem)      # "07outtakes"
        if m2:
            season, special = int(m2.group(1)), m2.group(2)

    items: list[dict] = []
    for i, line in enumerate(lines):
        if line.startswith(SKIP_PREFIXES):
            continue
        # 开头几行重复出现的标题（原稿常有拼写不一的两份）跳过
        if i < 3 and line.replace("mm", "m") == title.replace("mm", "m"):
            continue

        if STAGE_RE.match(line) and not SPEAKER_RE.match(line):
            text = line.strip("[]() ")
            # 只有 "Scene: 地点" 才是换场；"Time Lapse"/"Monica exits."
            # 这类是同场内的舞台提示，切分课程时不能当边界（全 10 季
            # 3094 条真换场 vs 3833 条提示）
            is_scene = bool(re.match(r"(?i)^scene\s*[:;]", text))
            items.append({
                "type": "scene" if is_scene else "stage",
                "text": re.sub(r"(?i)^scene\s*[:;]\s*", "", text) if is_scene else text,
            })
            continue

        sm = SPEAKER_RE.match(line)
        if sm and len(sm.group(1).split()) <= 3:
            # 表演提示两种写法都要收：冒号前 "Ross (愣住): Hi"
            # 和更常见的冒号后 "Ross: (愣住) Hi"
            direction = (sm.group(2) or "").strip("() ")
            text = sm.group(3).strip()
            lead = re.match(r"^\(([^)]{1,120})\)\s*(.*)$", text)
            if lead:
                direction = "; ".join(filter(None, [direction, lead.group(1)]))
                text = lead.group(2).strip()
            items.append({
                "type": "line",
                "speaker": canon_speaker(sm.group(1)),
                "direction": direction or None,
                "text": text,
            })
        elif items and items[-1]["type"] == "line" and not line.isupper():
            items[-1]["text"] += " " + line          # 台词跨行续接
        else:
            items.append({"type": "note", "text": line})

    lines_only = [i for i in items if i["type"] == "line"]
    return {
        "id": stem,
        "season": season,
        "episode": episode,
        "special": special,          # uncut / outtakes 等非常规版本
        "title": title,
        "source": "fangj/friends (fan transcript)",
        "stats": {
            "lines": len(lines_only),
            "scenes": sum(1 for i in items if i["type"] == "scene"),
            "stages": sum(1 for i in items if i["type"] == "stage"),
            "speakers": sorted({i["speaker"] for i in lines_only}),
        },
        "items": items,
    }


def render(doc: dict) -> str:
    """转成人眼可读的对白文本。"""
    buf = [doc["title"], "=" * len(doc["title"]), ""]
    for it in doc["items"]:
        if it["type"] == "line":
            head = it["speaker"] + (f" ({it['direction']})" if it["direction"] else "")
            buf.append(f"{head}: {it['text']}")
        elif it["type"] == "scene":
            buf += ["", f"[Scene: {it['text']}]", ""]
        elif it["type"] == "stage":
            buf += ["", f"({it['text']})", ""]
        else:
            buf += ["", f"-- {it['text']} --", ""]
    return "\n".join(buf) + "\n"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    want_text = "--text" in sys.argv

    if not SRC_DIR.is_dir():
        print(f"找不到剧本源目录: {SRC_DIR}\n"
              f"先执行: git clone --depth 1 "
              f"https://github.com/fangj/friends.git data/friends/github")
        return 1

    files = ([SRC_DIR / f"{a}.html" for a in args] if args
             else sorted(SRC_DIR.glob("*.html")))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    total_lines, done, failed = 0, 0, []
    for f in files:
        if not f.exists():
            failed.append(f"{f.name} (不存在)")
            continue
        try:
            doc = parse(f)
        except Exception as e:                       # noqa: BLE001
            failed.append(f"{f.name} ({e})")
            continue

        (OUT_DIR / f"{doc['id']}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        if want_text:
            (OUT_DIR / f"{doc['id']}.txt").write_text(render(doc), encoding="utf-8")

        total_lines += doc["stats"]["lines"]
        done += 1
        if len(files) <= 5:
            print(f"{doc['id']} S{doc['season']}E{doc['episode']} "
                  f"{doc['stats']['lines']} 句 / {doc['stats']['scenes']} 场景 "
                  f"— {doc['title'][:50]}")

    print(f"\n完成 {done}/{len(files)} 集，共 {total_lines} 句台词 → "
          f"{OUT_DIR.relative_to(ROOT)}/")
    if failed:
        print(f"失败 {len(failed)}: {', '.join(failed[:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
