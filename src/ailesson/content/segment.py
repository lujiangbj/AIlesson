"""把一集剧本切成若干「学习段」，每段对应一节课的素材范围。

为什么要切：Friends 一集 300 句、300+ 生词，按 PRD 每节 6 个重点词算要 50 节课，
「一集 = N 节课」的映射直接爆掉。切成 5 段后每段规模接近 peppa 一集。

怎么切：逐字稿没有时间轴（不是字幕文件，没时间戳），所以用**词数**近似时长
——语速大致恒定，比按句数准（对白密度差 3 倍：有的场景 24 词/句，有的 6.8 词/句）。
切点吸附到最近的换场边界，保证语义内聚（PRD FR-3.3 要求同一节的词属于同一场景）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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
