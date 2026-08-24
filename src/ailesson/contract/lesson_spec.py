"""课程契约：组课的产物 = 教室端的输入。

为什么单独成一层：这是「组课」和「教室端」唯一的接触面。放在 course/ 里，
教室端就得反向依赖组课，两块永远分不开；放这里，教室端只需要知道
「一节课有哪些教学点」，不关心它是 LLM 聚类出来的还是手工编的。

这里只放**结构**，不放生成逻辑（那在 course/planner.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LessonSpec:
    """一节课的三层内容清单。"""

    episode_id: str
    index: int
    theme: str
    focus_words: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    sentence_ids: list[str] = field(default_factory=list)
    # 顺带过的（1 题、不跟读）
    bonus_words: list[str] = field(default_factory=list)
    bonus_chunks: list[str] = field(default_factory=list)
    bonus_sentences: list[str] = field(default_factory=list)

    @property
    def lesson_id(self) -> str:
        return f"{self.episode_id}-L{self.index}"

    @property
    def n_points(self) -> int:
        return len(self.focus_words) + len(self.chunk_ids) + len(self.sentence_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "index": self.index,
            "theme": self.theme,
            "focus_words": self.focus_words,
            "chunk_ids": self.chunk_ids,
            "sentence_ids": self.sentence_ids,
            "bonus_words": self.bonus_words,
            "bonus_chunks": self.bonus_chunks,
            "bonus_sentences": self.bonus_sentences,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LessonSpec:
        return cls(
            episode_id=d["episode_id"],
            index=d["index"],
            theme=d.get("theme", ""),
            focus_words=list(d.get("focus_words", [])),
            chunk_ids=list(d.get("chunk_ids", [])),
            sentence_ids=list(d.get("sentence_ids", [])),
            bonus_words=list(d.get("bonus_words", [])),
            bonus_chunks=list(d.get("bonus_chunks", [])),
            bonus_sentences=list(d.get("bonus_sentences", [])),
        )

    def items(self) -> dict[str, list[str]]:
        """三层正课教学点，域 → id 列表。检查器和完备度矩阵都要按域遍历。"""
        return {
            "words": list(self.focus_words),
            "chunks": list(self.chunk_ids),
            "sentences": list(self.sentence_ids),
        }

    def bonus_items(self) -> dict[str, list[str]]:
        return {
            "words": list(self.bonus_words),
            "chunks": list(self.bonus_chunks),
            "sentences": list(self.bonus_sentences),
        }


@dataclass
class CoursePlan:
    """一集拆成的 N 节课。

    fallback=True 表示 LLM 分组失败、走了机械划分 —— 这个信号必须能传到前端，
    否则用户拿到「第1组 / 补充N」的烂课表却不知道为什么。
    """

    episode_id: str
    lessons: list[LessonSpec]
    at: int = 0
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "lessons": [l.to_dict() for l in self.lessons],
            "at": self.at,
            "fallback": self.fallback,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CoursePlan:
        return cls(
            episode_id=d["episode_id"],
            lessons=[LessonSpec.from_dict(x) for x in d.get("lessons", [])],
            at=d.get("at", 0),
            fallback=bool(d.get("fallback", False)),
        )

    def lesson(self, index: int) -> LessonSpec | None:
        return next((l for l in self.lessons if l.index == index), None)


__all__ = ["LessonSpec", "CoursePlan"]
