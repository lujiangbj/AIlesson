"""卡片模型：一张卡 = 屏幕上的一屏。

`kind` 拆掉了。它原先一身三职 —— 既表示交互形态（shadow）、又表示域
（chunk / sentence）、又表示方向（a2i / i2a）。后果是运行时里出现
`kind in ("a2i","i2a","chunk","sentence")` 这种想说「这是道选择题」却只能穷举
的判断，加一件教具要同时改三处映射。

现在三个维度各自独立：

    tool       用哪件教具（classroom/tools.py 的 id）
    domain     哪一层（words / chunks / sentences）
    direction  哪个方向（a2i / i2a / none）—— 掌握度按方向记

交互形态不存字段，从教具查（`interaction` property）：教具表是唯一事实来源，
存一份副本就会漂。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ailesson.contract.tools import tool
from ailesson.contract.episode import Episode


@dataclass
class Card:
    """一张卡 = 屏幕上的一屏。"""

    card_id: str
    step_index: int
    tool: str                    # 教具 id
    domain: str                  # words / chunks / sentences
    item_id: str
    direction: str = "none"      # a2i / i2a / none
    target_words: tuple[str, ...] = ()   # 这张卡教到哪些词（用于 5 次曝光统计）
    prompt_audio: str = ""
    prompt_audio_slow: str = ""
    image: str = ""
    meaning_zh: str = ""
    text: str = ""               # 仅跟读/管理用，不展示给学生
    choices: tuple[str, ...] = ()
    correct_id: str = ""
    audio_clips: tuple[str, ...] = ()
    needs_answer: bool = True
    is_bonus: bool = False       # 顺带词（FR-3.4：1 题、不跟读）

    @property
    def interaction(self) -> str:
        """交互形态，前端按它分派渲染器。"""
        return tool(self.tool).interaction

    @property
    def is_quiz(self) -> bool:
        return self.interaction == "quiz"

    @property
    def tool_name(self) -> str:
        return tool(self.tool).name

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "step_index": self.step_index,
            "tool": self.tool,
            "tool_name": self.tool_name,
            "interaction": self.interaction,
            "domain": self.domain,
            "item_id": self.item_id,
            "direction": self.direction,
            "target_words": list(self.target_words),
            "prompt_audio": self.prompt_audio,
            "prompt_audio_slow": self.prompt_audio_slow,
            "image": self.image,
            "meaning_zh": self.meaning_zh,
            "text": self.text,
            "choices": list(self.choices),
            "correct_id": self.correct_id,
            "audio_clips": list(self.audio_clips),
            "needs_answer": self.needs_answer,
            "is_bonus": self.is_bonus,
        }


def choices_for_word(ep: Episode, lemma: str, siblings: list[str]) -> tuple[str, ...]:
    """4 选 1 的选项：优先用素材里的 distractors（难度已经调好），不够拿同节词补。"""
    out = [lemma]
    for d in ep.distractors_for(lemma):
        if d != lemma and d not in out:
            out.append(d)
    for s in siblings:
        if len(out) >= 4:
            break
        if s != lemma and s not in out:
            out.append(s)
    for w in ep.words:
        if len(out) >= 4:
            break
        if w.lemma != lemma and w.lemma not in out:
            out.append(w.lemma)
    return tuple(out[:4])


def choices_for_item(all_ids: list[str], cid: str, pool: list[str]) -> tuple[str, ...]:
    """短语 / 句子的 4 选 1：先取同池条目（场景相近、干扰强），不足拿全集补。

    一节只有 2 个短语时也必须凑满 4 个 —— 否则 2 选 1 蒙对率 50%，
    1 个短语时只剩唯一选项（点谁都对）。
    """
    out = [cid]
    for x in pool:
        if x != cid and x not in out:
            out.append(x)
    for x in all_ids:
        if len(out) >= 4:
            break
        if x != cid and x not in out:
            out.append(x)
    return tuple(out[:4])


__all__ = ["Card", "choices_for_item", "choices_for_word"]
