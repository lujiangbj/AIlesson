"""听力探测：抽样测 chunk / 句子的掌握度，再推断其余。

为什么需要（用户 2026-08-14 指出）：
我早先用「单词掌握情况」推断 chunk 和句子难度，那是**推断不是测量**。
三种能力是分开的——认识 crash / on / couch 每个词，也听不懂
"You gonna crash on the couch?"，因为难点在习语和连读。

但让用户逐条勾 114 个 chunk + 76 句太累。所以：
1. **抽样探测**：按难度分层挑 ~16 条，放音频，用户答「听懂/没听懂」
2. **校准**：从探测结果反推这个人的能力边界（阈值）
3. **推断**：用校准后的阈值判断其余条目

探测用听力而非看文本，因为课程教的就是听——PRD 环节 3/6/9 全是听辨。
这也和 FR-2「会词抽检」同一思路：自评不可信，要实测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# 连读弱读的主要来源
CONTRACTION_RE = re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.I)
# 口语缩合与习语：听力的真正门槛，跟单词量无关
IDIOM_CUES = (
    "gonna", "wanna", "gotta", "kinda", "sorta", "outta", "lemme", "gimme",
    "ain't", "y'know", "c'mon", "'em", "cuz", "'cause",
    "supposed to", "kind of", "sort of", "a bunch of", "come on",
    "how come", "what if", "used to", "would've", "could've", "should've",
    "no way", "big deal", "hang on", "hold on", "check it out",
)
# 易被吞音/同化的高频功能词组合
REDUCTION_CUES = ("of the", "in the", "to the", "and the", "on the",
                  "want to", "going to", "have to", "has to", "did you",
                  "don't you", "what are you", "let me")

PROBE_N = 16          # 探测条数：够分层又不累人
BANDS = 4             # 难度分层数
# 校准不可信时的保守阈值。只把明显难的判为不会，免得把整集塞进待学池
FALLBACK_THRESHOLD = 4.0


@dataclass
class Features:
    """一条 chunk/句子的听力难度特征。"""
    n_words: int = 0
    n_unknown: int = 0
    n_idiom: int = 0
    n_contraction: int = 0
    n_reduction: int = 0

    @property
    def unknown_ratio(self) -> float:
        return self.n_unknown / self.n_words if self.n_words else 0.0

    def to_dict(self) -> dict:
        return {"n_words": self.n_words, "n_unknown": self.n_unknown,
                "n_idiom": self.n_idiom, "n_contraction": self.n_contraction,
                "n_reduction": self.n_reduction}


def features(text: str, unknown_words: set[str]) -> Features:
    toks = [w.lower() for w in WORD_RE.findall(text)]
    low = text.lower()
    return Features(
        n_words=len(toks),
        n_unknown=sum(1 for t in toks if t in unknown_words),
        n_idiom=sum(1 for c in IDIOM_CUES if c in low),
        n_contraction=len(CONTRACTION_RE.findall(text)),
        n_reduction=sum(1 for c in REDUCTION_CUES if c in low),
    )


# 各特征对听力难度的权重。生词只占一部分——习语和连读是独立难点，
# 这正是不能只靠单词掌握度推断的原因
W_UNKNOWN_RATIO = 6.0
W_UNKNOWN_ABS = 0.8
W_IDIOM = 1.6
W_CONTRACTION = 0.7
W_REDUCTION = 0.5
W_LENGTH = 0.12


def difficulty(f: Features) -> float:
    """听力难度分。越高越难听懂。"""
    return (W_UNKNOWN_RATIO * f.unknown_ratio
            + W_UNKNOWN_ABS * f.n_unknown
            + W_IDIOM * f.n_idiom
            + W_CONTRACTION * f.n_contraction
            + W_REDUCTION * f.n_reduction
            + W_LENGTH * max(0, f.n_words - 4))


@dataclass
class Item:
    """一个待探测/待推断的条目。"""
    id: str
    kind: str                 # "chunk" | "sentence"
    text: str
    diff: float = 0.0
    feats: Features = field(default_factory=Features)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "text": self.text,
                "difficulty": round(self.diff, 2), **self.feats.to_dict()}


def build_items(
    chunks: list[tuple[str, str]],
    sentences: list[tuple[str, str]],
    unknown_words: set[str],
) -> list[Item]:
    """把 (id, text) 列表转成带难度的 Item。"""
    out: list[Item] = []
    for kind, pairs in (("chunk", chunks), ("sentence", sentences)):
        for cid, text in pairs:
            f = features(text, unknown_words)
            out.append(Item(id=cid, kind=kind, text=text,
                            diff=difficulty(f), feats=f))
    return out


def stratified_probe(items: list[Item], n: int = PROBE_N,
                     bands: int = BANDS) -> list[Item]:
    """按难度分层抽样。

    均匀抽会集中在中等难度，测不出能力边界在哪。分层保证难易两端都有样本，
    校准才有信息量。chunk 和句子各占一半——两者难度分布不同。
    """
    if not items or n <= 0:
        return []

    picked: list[Item] = []
    for kind in ("chunk", "sentence"):
        pool = sorted((i for i in items if i.kind == kind),
                      key=lambda x: x.diff)
        if not pool:
            continue
        quota = max(1, n // 2)
        per_band = max(1, quota // bands)
        size = max(1, len(pool) // bands)
        for b in range(bands):
            seg = pool[b * size: (b + 1) * size] if b < bands - 1 \
                else pool[b * size:]
            if not seg:
                continue
            # 每层取中位数附近的，代表该层典型难度
            mid = len(seg) // 2
            for k in range(per_band):
                idx = mid + (k // 2 + 1) * (1 if k % 2 else -1) if k else mid
                if 0 <= idx < len(seg) and seg[idx] not in picked:
                    picked.append(seg[idx])
    return picked[:n]


@dataclass
class Calibration:
    """从探测结果算出的能力边界。"""
    threshold: float = 0.0
    accuracy: float = 0.0
    n_probed: int = 0
    n_understood: int = 0

    @property
    def confident(self) -> bool:
        """样本太少或答案没有区分度时，阈值不可信。"""
        return self.n_probed >= 6 and 0 < self.n_understood < self.n_probed

    def to_dict(self) -> dict:
        return {"threshold": round(self.threshold, 2),
                "accuracy": round(self.accuracy, 2),
                "n_probed": self.n_probed,
                "n_understood": self.n_understood,
                "confident": self.confident}


def calibrate(results: list[tuple[Item, bool]]) -> Calibration:
    """从「听懂/没听懂」反推阈值：难度 > threshold 的判为不会。

    找使探测集上判对率最高的切分点。答案有噪声（用户可能蒙对），
    所以不强求完美切分，取最优近似。
    """
    if not results:
        return Calibration()
    n = len(results)
    n_ok = sum(1 for _, ok in results if ok)

    diffs = sorted({r[0].diff for r in results})
    cands = [diffs[0] - 0.5] + [
        (a + b) / 2 for a, b in zip(diffs, diffs[1:])] + [diffs[-1] + 0.5]

    best_t, best_acc = cands[0], -1.0
    for t in cands:
        # 预测：diff <= t 判会，> t 判不会
        hit = sum(1 for it, ok in results if (it.diff <= t) == ok)
        acc = hit / n
        if acc > best_acc:
            best_t, best_acc = t, acc
    return Calibration(threshold=best_t, accuracy=best_acc,
                       n_probed=n, n_understood=n_ok)


def infer_unknown(
    items: list[Item],
    cal: Calibration,
    probed: dict[str, bool] | None = None,
) -> dict[str, list[str]]:
    """用校准阈值推断没探测过的条目，已探测的用实测结果。

    返回 {"chunks": [...], "sentences": [...]} 的待学 id 列表。
    """
    probed = probed or {}
    out: dict[str, list[str]] = {"chunks": [], "sentences": []}
    slot = {"chunk": "chunks", "sentence": "sentences"}

    for it in items:
        if it.id in probed:
            unknown = not probed[it.id]          # 实测优先
        elif cal.confident:
            unknown = it.diff > cal.threshold
        else:
            # 阈值不可信（样本太少 / 答案无区分度）时用保守默认值。
            # 不能退回 cal.threshold——它可能是 0.0，会把整集都判成待学
            unknown = it.diff > FALLBACK_THRESHOLD
        if unknown:
            out[slot[it.kind]].append(it.id)
    return out
