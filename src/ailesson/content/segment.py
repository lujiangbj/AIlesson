"""把一集剧本切成若干「学习段」，每段对应一节课的素材范围。

为什么要切：Friends 一集 300 句、300+ 生词，按 PRD 每节 6 个重点词算要 50 节课，
「一集 = N 节课」的映射直接爆掉。切成 5 段后每段规模接近 peppa 一集。

怎么切：逐字稿没有时间轴（不是字幕文件，没时间戳），所以用**词数**近似时长
——语速大致恒定，比按句数准（对白密度差 3 倍：有的场景 24 词/句，有的 6.8 词/句）。
切点吸附到最近的换场边界，保证语义内聚（PRD FR-3.3 要求同一节的词属于同一场景）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


@dataclass
class Chunk:
    """一个换场到下一个换场之间的内容。切分的最小单位。"""
    scene: str | None                    # 换场地点，None 表示开场未标注
    items: list[dict] = field(default_factory=list)

    @property
    def lines(self) -> list[dict]:
        return [i for i in self.items if i["type"] == "line"]

    @property
    def words(self) -> int:
        return sum(len(WORD_RE.findall(i["text"])) for i in self.lines)

    @property
    def location(self) -> str:
        """地点简称，去掉人物走位描述。"""
        if not self.scene:
            return "开场"
        return re.split(r"[,;.]", self.scene)[0].strip()[:28]


@dataclass
class Segment:
    """一个学习段，含若干连续 chunk。"""
    index: int
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(c.words for c in self.chunks)

    @property
    def lines(self) -> list[dict]:
        return [ln for c in self.chunks for ln in c.lines]

    @property
    def locations(self) -> list[str]:
        """段内出现的地点，去重保序。"""
        out: list[str] = []
        for c in self.chunks:
            if c.location not in out:
                out.append(c.location)
        return out

    @property
    def texts(self) -> list[str]:
        return [ln["text"] for ln in self.lines]

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "words": self.words,
            "lines": len(self.lines),
            "locations": self.locations,
            "scenes": [c.scene for c in self.chunks if c.scene],
            # chunk 数要显式存：scenes 会丢掉开场那个没有场景标记的 chunk，
            # 拿 len(scenes) 反推段边界会错位，取到别的段的台词
            "n_chunks": len(self.chunks),
        }


def split_chunks(items: list[dict]) -> list[Chunk]:
    """按 type=="scene"（真换场）把 items 切成 chunk。

    type=="stage" 的舞台提示（Time Lapse / Monica exits）不算边界，
    它们是同场内的时间跳跃或动作，切在那里会把一段对话拦腰截断。
    """
    chunks: list[Chunk] = [Chunk(scene=None)]
    for it in items:
        if it["type"] == "scene":
            chunks.append(Chunk(scene=it["text"]))
        else:
            chunks[-1].items.append(it)
    # 丢掉没台词的空 chunk（开场注释、连续换场标记）
    return [c for c in chunks if c.lines]


def _partition(sizes: list[int], n: int) -> list[int]:
    """把 sizes 切成 n 段连续区间，最小化各段与均值的平方差。

    返回每段的 chunk 数量。用 DP 求全局最优——贪心会在末尾堆积余量
    （前面每段都吃到目标量，最后一段捡剩下的），段数少时差别很明显。
    chunk 数不多（一集十几个），O(n·m²) 完全够用。
    """
    m = len(sizes)
    target = sum(sizes) / n
    prefix = [0]
    for s in sizes:
        prefix.append(prefix[-1] + s)

    def cost(i: int, j: int) -> float:
        """chunk[i:j] 作为一段的代价。"""
        return (prefix[j] - prefix[i] - target) ** 2

    INF = float("inf")
    # dp[k][j] = 前 j 个 chunk 切成 k 段的最小代价
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for k in range(1, n + 1):
        for j in range(k, m - (n - k) + 1):
            for i in range(k - 1, j):
                if dp[k - 1][i] == INF:
                    continue
                c = dp[k - 1][i] + cost(i, j)
                if c < dp[k][j]:
                    dp[k][j] = c
                    back[k][j] = i

    counts: list[int] = []
    j = m
    for k in range(n, 0, -1):
        i = back[k][j]
        counts.append(j - i)
        j = i
    return list(reversed(counts))


def _build(chunks: list[Chunk], counts: list[int]) -> list[Segment]:
    segs, pos = [], 0
    for idx, cnt in enumerate(counts, 1):
        segs.append(Segment(index=idx, chunks=chunks[pos:pos + cnt]))
        pos += cnt
    return segs


def spread(segs: list[Segment]) -> float:
    """不均衡度：最大段词数 / 平均段词数。1.0 是完美均分。"""
    if not segs:
        return 0.0
    words = [s.words for s in segs]
    avg = sum(words) / len(words)
    return max(words) / avg if avg else 0.0


def _line_brief(ln: dict) -> dict:
    """一句话的摘要。核对切点时只需要看是谁说的、说了什么。"""
    return {
        "speaker": ln.get("speaker") or "",
        "text": (ln.get("text") or "")[:120],
    }


def segment_episode(
    items: list[dict],
    n: int | None = None,
    *,
    n_range: tuple[int, int] = (4, 6),
) -> list[Segment]:
    """把一集切成若干学习段，切点只落在换场边界（不会断在场景中途）。

    n 给定则切固定份数；不给则在 n_range 内搜最均匀的方案。
    份数不必是 5——4 段或 6 段若切得更齐，就用那个。
    """
    chunks = split_chunks(items)
    if not chunks:
        return []

    if n is not None:
        n = max(1, min(n, len(chunks)))
        return _build(chunks, _partition([c.words for c in chunks], n))

    sizes = [c.words for c in chunks]
    lo, hi = n_range
    hi = min(hi, len(chunks))
    lo = max(1, min(lo, hi))

    best: list[Segment] | None = None
    for k in range(lo, hi + 1):
        cand = _build(chunks, _partition(sizes, k))
        # 均匀优先；接近时取段数少的（每节课素材更足）
        if best is None or spread(cand) < spread(best) - 0.02:
            best = cand
    return best or []


# ---------------- 切段结果落盘 ----------------
#
# 原先切段只打印在终端里，下游（选词 / 选句 / 配图）拿不到段边界，只能整集处理
# ——「一集 = 多段，每段 = N 节课」这层结构在数据里根本不存在。
#
# 落盘后：后台能看见拆分效果，下游能按段取素材，规则改了能对比两版差异。

# 当前切段规则的说明，随计划落盘。规则会变，存下来才知道这份是怎么切的
RULE = "词数均分（DP 最优）+ 切点吸附换场边界；逐字稿无时间轴，用词数近似时长"

# 一集的估算时长（分钟），用于把词数换算成每段时长
RUNTIME_MIN = 24

# 每节课的重点词数，用于估算一段能出几节课（PRD FR-3.1）
WORDS_PER_LESSON = 6

# 生词口径：这一级及以下算已会
KNOWN_LEVEL = "A1"


@dataclass
class SegmentPlan:
    """一集的切段计划。这是「剧本 → 后台」的交接物。"""

    episode_id: str
    n: int
    auto: bool                      # 段数是自动选的还是指定的
    spread: float                   # 不均衡度，1.0 是完美均分
    total_lines: int
    total_words: int
    runtime_min: float
    rule: str
    segments: list[dict] = field(default_factory=list)
    title: str = ""

    @classmethod
    def build(
        cls,
        episode_id: str,
        items: list[dict],
        n: int | None = None,
        *,
        levels: dict[str, str] | None = None,
        runtime_min: float = RUNTIME_MIN,
        title: str = "",
    ) -> SegmentPlan:
        segs = segment_episode(items, n)
        total_words = sum(s.words for s in segs) or 1
        rows: list[dict] = []
        for s in segs:
            lines = s.lines
            row = {
                **s.to_dict(),
                "minutes": round(s.words / total_words * runtime_min, 1),
                "first_line": _line_brief(lines[0]) if lines else None,
                "last_line": _line_brief(lines[-1]) if lines else None,
                "new_words": None,
                "est_lessons": None,
            }
            if levels is not None:
                # 只在这里 import：vocab_cefr 也在 content 层，但切段本身
                # 不该依赖词表 —— 没词表时切段照样能跑
                from ailesson.content.vocab_cefr import token_freq

                new = {w for w in token_freq(s.texts)
                       if levels.get(w) and levels[w] != KNOWN_LEVEL}
                row["new_words"] = len(new)
                row["est_lessons"] = round(len(new) / WORDS_PER_LESSON, 1)
            rows.append(row)

        return cls(
            episode_id=episode_id,
            n=len(segs),
            auto=n is None,
            spread=round(spread(segs), 3),
            total_lines=sum(len(s.lines) for s in segs),
            total_words=sum(s.words for s in segs),
            runtime_min=runtime_min,
            rule=RULE,
            segments=rows,
            title=title,
        )

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "title": self.title,
            "n": self.n,
            "auto": self.auto,
            "spread": self.spread,
            "total_lines": self.total_lines,
            "total_words": self.total_words,
            "runtime_min": self.runtime_min,
            "rule": self.rule,
            "segments": self.segments,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SegmentPlan:
        return cls(
            episode_id=d["episode_id"],
            title=d.get("title", ""),
            n=d["n"],
            auto=bool(d.get("auto", False)),
            spread=d.get("spread", 0.0),
            total_lines=d.get("total_lines", 0),
            total_words=d.get("total_words", 0),
            runtime_min=d.get("runtime_min", RUNTIME_MIN),
            rule=d.get("rule", ""),
            segments=list(d.get("segments", [])),
        )


def save_plan(plan: SegmentPlan, root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{plan.episode_id}.json"
    p.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=1))
    return p


def load_plan(episode_id: str, root: Path) -> SegmentPlan | None:
    """读回切段计划。没存过或文件坏了都回 None —— 后台要能显示「还没切」。"""
    p = Path(root) / f"{episode_id}.json"
    if not p.exists():
        return None
    try:
        return SegmentPlan.from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, KeyError):
        return None


__all__ = [
    "KNOWN_LEVEL", "RULE", "RUNTIME_MIN", "WORDS_PER_LESSON",
    "Chunk", "Segment", "SegmentPlan",
    "load_plan", "save_plan", "segment_episode", "split_chunks", "spread",
]
