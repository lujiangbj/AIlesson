"""教具表：一节课是由教具拼起来的，教具是一等对象。

原先这些信息散成三处：`Card.kind` 的字符串、`_build_cards` 里的分支、前端的
`KIND_HINT`/`CARDS` 两张映射。加一种教具要同时改三处，而「这个教具需要什么素材」
只存在于 friends_lesson.py 的一句注释里（「图和音都齐才收」）。

收成一张表之后有两个直接好处：

1. **内容完备度可计算** —— 教具声明 `needs`，就能算出某集某教学点能跑哪些教具、
   缺什么素材。这是教研后台的核心视图，也是老友记线卡住的地方（没时间轴 →
   没 audio_clip → 环节 10 的句子原声跑不了）。
2. **环节只需引用教具 id** —— 编排从代码变成配置（见 arrangement.py）。

`kind` 一身三职的旧账在这里还掉：教具（interaction）、域（domain）、方向
（direction）拆成三个独立维度。旧的 `kind="chunk"` 同时表示「选择题」和
「短语域」，导致 runtime 里出现 `kind in ("a2i","i2a","chunk","sentence")`
这种想说「这是道选择题」却只能穷举的判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 交互形态 —— 前端按这个分派渲染器，新增形态才需要动前端
Interaction = Literal["quiz", "shadow", "passive", "assess", "report"]
INTERACTIONS: tuple[str, ...] = ("quiz", "shadow", "passive", "assess", "report")

# 素材需求的取值。对应 Episode 里的字段
Asset = Literal["audio", "audio_slow", "audio_clip", "image", "meaning_zh", "text"]

Direction = Literal["a2i", "i2a", "none"]
DOMAINS: tuple[str, ...] = ("words", "chunks", "sentences")


@dataclass(frozen=True)
class Tool:
    """一件教具 = 一种「屏幕上发生什么」。

    id 会落进快照（Card.tool），改名等于换教材，按 arrangement 的版本规则处理。
    """

    id: str
    name: str                       # 中文名，直接显示给用户，不露 id
    interaction: Interaction
    direction: Direction
    domains: tuple[str, ...]        # 哪些域能用这件教具
    needs: tuple[str, ...]          # 素材需求，完备度矩阵靠它算
    hint: str = ""                  # 给学习者的一句操作提示
    note: str = ""                  # 给教研/开发看的说明

    @property
    def scored(self) -> bool:
        """是否计 streak。

        只有答题计 —— 跟读练产出、被动播放和自评不构成「答对」。
        环节还能进一步关掉计分（巩固类环节），见 arrangement.Step.scored。
        """
        return self.interaction == "quiz"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "interaction": self.interaction,
            "direction": self.direction,
            "domains": list(self.domains),
            "needs": list(self.needs),
            "hint": self.hint,
            "note": self.note,
            "scored": self.scored,
        }


_ALL: tuple[Tool, ...] = (
    Tool(
        id="listen_pick_image",
        name="听音选图",
        interaction="quiz", direction="a2i",
        domains=("words",),
        needs=("audio", "image"),
        hint="听声音，选出对应的图片",
        note="词的首触形态。选项是图，所以作答前不能显示题干图 —— 那就是答案。",
    ),
    Tool(
        id="listen_pick_meaning",
        name="听音选义",
        interaction="quiz", direction="a2i",
        domains=("chunks", "sentences"),
        needs=("audio", "meaning_zh"),
        hint="听声音，选出对应的中文释义",
        note="短语和句子不用图选项 —— 四张场景图区分度不够，选不出来。",
    ),
    Tool(
        id="recall_pick_audio",
        name="看义选音",
        interaction="quiz", direction="i2a",
        domains=("words", "chunks", "sentences"),
        needs=("audio", "meaning_zh"),
        hint="先点 🔊 试听选项，再点选项作答",
        note="反向：逼「语义 → 声音」的检索方向。只会听音选图不算学会。",
    ),
    Tool(
        id="shadow",
        name="跟读",
        interaction="shadow", direction="none",
        domains=("words", "chunks", "sentences"),
        needs=("audio", "text"),
        hint="先听一遍，再自己念出来",
        note="评分未接（当前最大缺口）。现在只有「念好了 / 念不出来」两个按钮。",
    ),
    Tool(
        id="watch_clip",
        name="看片段",
        interaction="passive", direction="none",
        domains=("sentences",),
        needs=("audio_clip", "image"),
        hint="歇一下，看画面听原片",
        note="中场降载。必须是原片切片，TTS 起不到「听真实语流」的作用。",
    ),
    Tool(
        id="blind_listen",
        name="场景盲听",
        interaction="assess", direction="none",
        domains=("sentences",),
        needs=("audio_clip",),
        hint="连着听一遍本节的句子，自己判断听懂多少",
        note="自评，不计分。课内不产生「已掌握」。",
    ),
    Tool(
        id="report",
        name="课后报告",
        interaction="report", direction="none",
        domains=("words", "chunks", "sentences"),
        needs=(),
        note="收尾。口径见 report.py：不说「掌握」，按三层分别说。",
    ),
)

TOOLS: dict[str, Tool] = {t.id: t for t in _ALL}


def tool(tool_id: str) -> Tool:
    """按 id 取教具。查不到就抛 —— 编排里写错 id 应该立刻炸，不该静默降级。"""
    try:
        return TOOLS[tool_id]
    except KeyError:
        raise KeyError(
            f"没有教具 {tool_id!r}；可用：{sorted(TOOLS)}"
        ) from None


def tools_for_domain(domain: str) -> tuple[Tool, ...]:
    return tuple(t for t in _ALL if domain in t.domains)


def tool_for(direction: str, domain: str) -> Tool:
    """按「方向 + 域」定位答题教具。

    编排里的复习 / 混打环节的方向是按 streak 算出来的（较弱方向优先），
    不能在配置里写死教具 id，只能声明方向策略，到这里换成具体教具。
    """
    for t in _ALL:
        if t.interaction == "quiz" and t.direction == direction \
                and domain in t.domains:
            return t
    raise KeyError(f"没有 {direction} 方向、适用于 {domain} 的答题教具")


def missing_assets(t: Tool, have: set[str]) -> tuple[str, ...]:
    """这件教具还缺哪些素材。按声明顺序返回，便于稳定展示。"""
    return tuple(a for a in t.needs if a not in have)


__all__ = [
    "DOMAINS", "INTERACTIONS", "TOOLS", "Tool",
    "missing_assets", "tool", "tool_for", "tools_for_domain",
]
