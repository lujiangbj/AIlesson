"""编排：一节课由哪些环节、按什么顺序、各用什么教具组成。

原先这些信息是代码：`SEGMENTS` 元组定顺序和标题，`_build_cards` 的分支决定每个
环节发什么卡，`STREAK_SEGMENTS = {1,2,3,4,6,7,10,11}` 和
`if card.segment_index in (3, 6, 10)` 这类魔数散在运行时里。改一个环节要同时找
四五处。

现在环节声明成数据，魔数变成环节属性：

    scored       这一环计不计 streak（原 STREAK_SEGMENTS）
    first_touch  这一环算不算首触（原 `in (3, 6, 10)`）
    source       内容从哪来（本节正课 / 复习 / 抽检 / 错题 / 混打）
    domains      发哪几层的卡（复习发三层，混打发词 + 短语）

## 版本号不是装饰

编排一旦可编辑，就撞上 §10.5 的升级版问题。运行时的卡序是**确定性重建**的：
快照只存重建输入，牌靠 `_build_cards` 重算。前提是编排恒定。上周的快照拿这周
改过的编排去 restore，会重建出一副不同的牌 —— 续上错位，而且不会报错。

所以：**改编排 = 换教材**。快照记 `arrangement_id` + `version`，不匹配就不许
重建（`compatible()`）。这不是新规矩，是 §10.5「卡序必须确定性可重建」的延伸。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ailesson.contract.tools import TOOLS, Tool, tool, tool_for

# 环节内容的来源
#   focus   本节正课教学点（+ 顺带点）
#   review  跨节复习（按 streak 挑，较弱方向优先）
#   spot    抽检已勾会的（防自评造假）
#   mixed   本节 + 复习混打
#   redo    本节错题重做
#   single  取本节第一个句子（中场用）
#   all     本节全部句子（盲听用）
#   none    不需要内容（报告）
Source = Literal["focus", "review", "spot", "mixed", "redo", "single", "all", "none"]

# 方向策略：写死 a2i / i2a，或按 streak 取较弱方向
DirPolicy = Literal["a2i", "i2a", "weaker"]

# tool 字段的两个特殊值
BY_DIRECTION = "*"      # 教具由方向策略 + 域在运行时定
INHERIT = "^"           # 沿用该教学点首触时用的教具（重做环节）


@dataclass(frozen=True)
class Step:
    """一个环节 = 一件教具的一次实例化 + 内容来源。"""

    index: int
    key: str                        # 稳定标识，落进快照和日志
    title: str                      # 中文名，显示给学习者
    minutes: float
    source: Source
    domains: tuple[str, ...]        # 发哪几层的卡
    tool: str                       # 教具 id，或 BY_DIRECTION / INHERIT
    direction: DirPolicy = "a2i"
    scored: bool = False            # 计 streak（原 STREAK_SEGMENTS）
    first_touch: bool = False       # 算首触，进「一次答对」统计（原魔数 3/6/10）
    note: str = ""

    @property
    def by_direction(self) -> bool:
        return self.tool == BY_DIRECTION

    @property
    def inherit(self) -> bool:
        return self.tool == INHERIT

    def directions(self) -> tuple[str, ...]:
        """这一环可能出现的方向。"""
        if self.direction == "weaker":
            return ("a2i", "i2a")
        return (self.direction,)

    def tool_ids(self) -> tuple[str, ...]:
        """这一环可能用到的教具。

        按方向定的会展开成候选集合；沿用首触的返回空 —— 它不引入新教具，
        也不引入新的素材需求。
        """
        if self.inherit:
            return ()
        if not self.by_direction:
            return (self.tool,)
        out: list[str] = []
        for d in self.directions():
            for dom in self.domains:
                try:
                    t = tool_for(d, dom)
                except KeyError:
                    continue
                if t.id not in out:
                    out.append(t.id)
        return tuple(out)

    def resolve(self, domain: str, direction: str | None = None) -> Tool:
        """定位这一环对某个域实际要用的教具。

        direction 只在策略是 weaker 时才需要（调用方从
        `progress.weaker_direction()` 拿）。
        """
        if self.inherit:
            raise ValueError(
                f"环节 {self.index}（{self.title}）沿用首触教具，"
                "该由调用方从 proto 卡取，不能在这里定"
            )
        if not self.by_direction:
            return tool(self.tool)
        d = direction if self.direction == "weaker" else self.direction
        if d is None:
            raise ValueError(
                f"环节 {self.index}（{self.title}）的教具按方向定，必须传 direction"
            )
        return tool_for(d, domain)

    def needs(self) -> tuple[str, ...]:
        """这一环用到的全部素材需求，按声明顺序去重。"""
        out: list[str] = []
        for tid in self.tool_ids():
            for a in TOOLS[tid].needs:
                if a not in out:
                    out.append(a)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        ids = self.tool_ids()
        if self.inherit:
            name = "沿用首触教具"
        else:
            name = " / ".join(TOOLS[t].name for t in ids)
        return {
            "index": self.index,
            "key": self.key,
            "title": self.title,
            "minutes": self.minutes,
            "source": self.source,
            "domains": list(self.domains),
            "tool": self.tool,
            "tool_ids": list(ids),
            "tool_name": name,
            "direction": self.direction,
            "scored": self.scored,
            "first_touch": self.first_touch,
            "needs": list(self.needs()),
            "note": self.note,
        }


@dataclass(frozen=True)
class Arrangement:
    """一套编排。id + version 一起构成「这是哪本教材的哪一版」。"""

    id: str
    version: int
    steps: tuple[Step, ...]
    title: str = ""
    note: str = ""

    def step(self, index: int) -> Step:
        for s in self.steps:
            if s.index == index:
                return s
        raise KeyError(f"编排 {self.id} 没有第 {index} 环节")

    def minutes(self) -> float:
        return round(sum(s.minutes for s in self.steps), 1)

    def scored_indexes(self) -> set[int]:
        return {s.index for s in self.steps if s.scored}

    def first_touch_indexes(self) -> set[int]:
        return {s.index for s in self.steps if s.first_touch}

    def stamp(self) -> dict[str, Any]:
        """写进快照的编排身份。"""
        return {"arrangement_id": self.id, "arrangement_version": self.version}

    def validate(self) -> None:
        """编排自检。配错了要在启动/保存时炸，不许留到上课中途。"""
        idx = [s.index for s in self.steps]
        if idx != list(range(1, len(idx) + 1)):
            raise ValueError(f"编排 {self.id} 的环节序号必须从 1 连续：{idx}")
        for s in self.steps:
            for tid in s.tool_ids():
                if tid not in TOOLS:
                    raise ValueError(
                        f"环节 {s.index}（{s.title}）引用了不存在的教具 {tid!r}"
                    )
            if s.inherit:
                continue
            if s.by_direction:
                if not s.tool_ids():
                    raise ValueError(
                        f"环节 {s.index}（{s.title}）按方向定教具，但 "
                        f"{s.direction} 方向在 {list(s.domains)} 上没有可用教具"
                    )
                continue
            t = tool(s.tool)
            for dom in s.domains:
                if dom not in t.domains:
                    raise ValueError(
                        f"环节 {s.index}（{s.title}）把教具「{t.name}」用在 "
                        f"{dom} 域上，它只支持 {list(t.domains)}"
                    )
            if s.scored and not t.scored:
                raise ValueError(
                    f"环节 {s.index}（{s.title}）标了计分，但教具"
                    f"「{t.name}」不是答题类，不产生对错"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "note": self.note,
            "minutes": self.minutes(),
            "steps": [s.to_dict() for s in self.steps],
        }


DEFAULT_ID = "standard-16"
DEFAULT_VERSION = 1

_W = ("words",)
_C = ("chunks",)
_S = ("sentences",)
_ALL3 = ("words", "chunks", "sentences")

# 三层各自走「首触 → 反向 → 跟读」，中间插中场降载，末尾混打 + 重做 + 盲听 + 报告。
# 时长合计 32 分钟（FR-4）。
DEFAULT = Arrangement(
    id=DEFAULT_ID,
    version=DEFAULT_VERSION,
    title="标准 16 环节（32 分钟）",
    note="改这张表 = 换教材，记得升 version，否则旧快照会静默错位。",
    steps=(
        Step(1, "review", "开场 + 复习", 3.0, "review", _ALL3, BY_DIRECTION,
             direction="weaker", scored=True,
             note="三层混合。取较弱方向 —— 只练强的那向等于白练。"),
        Step(2, "spot_check", "抽检", 1.0, "spot", ("words", "chunks"),
             BY_DIRECTION, direction="a2i", scored=True,
             note="抽检已勾会的。答错要降级回待学池（FR-2）。"),
        Step(3, "word_first", "生词首触", 2.0, "focus", _W,
             "listen_pick_image", scored=True, first_touch=True),
        Step(4, "word_recall", "生词反向", 1.5, "focus", _W,
             "recall_pick_audio", direction="i2a", scored=True),
        Step(5, "word_shadow", "生词跟读", 1.5, "focus", _W, "shadow"),
        Step(6, "chunk_first", "短语听辨", 3.0, "focus", _C,
             "listen_pick_meaning", scored=True, first_touch=True),
        Step(7, "chunk_recall", "短语反向", 2.0, "focus", _C,
             "recall_pick_audio", direction="i2a", scored=True),
        Step(8, "chunk_shadow", "短语跟读", 2.5, "focus", _C, "shadow"),
        Step(9, "interlude", "中场", 1.0, "single", _S, "watch_clip",
             note="降载。连着 8 环答题之后需要一个不用动脑的间隙。"),
        Step(10, "sentence_first", "句子原声", 3.5, "focus", _S,
             "listen_pick_meaning", scored=True, first_touch=True,
             note="必须用原片切片（FR-4.5）。听懂真实语流是产品核心。"),
        Step(11, "sentence_recall", "句子反向", 2.0, "focus", _S,
             "recall_pick_audio", direction="i2a", scored=True),
        Step(12, "sentence_shadow", "句子跟读", 3.0, "focus", _S, "shadow"),
        Step(13, "mixed", "混打", 2.0, "mixed", ("words", "chunks"),
             BY_DIRECTION, direction="weaker",
             note="巩固，不计 streak —— 课内不产生「已掌握」（§10.7）。"),
        Step(14, "redo", "错题重做", 1.5, "redo", _ALL3, INHERIT,
             note="沿用该点首触那张卡。答错仍清零，只是不计正向 streak。"),
        Step(15, "blind_listen", "场景盲听", 1.5, "all", _S, "blind_listen"),
        Step(16, "report", "收尾报告", 1.0, "none", (), "report"),
    ),
)

DEFAULT.validate()


def compatible(arr: Arrangement, snap: dict[str, Any]) -> bool:
    """快照能不能用这套编排重建。

    老快照（v0.6 前）没记编排字段，它们跑的就是默认编排 —— 按默认算兼容，
    不要求用户重开。
    """
    sid = snap.get("arrangement_id", DEFAULT_ID)
    ver = snap.get("arrangement_version", DEFAULT_VERSION)
    return sid == arr.id and ver == arr.version


__all__ = [
    "BY_DIRECTION", "DEFAULT", "DEFAULT_ID", "DEFAULT_VERSION", "INHERIT",
    "Arrangement", "Step", "compatible",
]
