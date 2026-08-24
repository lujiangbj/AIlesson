"""卡片模型：一张卡 = 屏幕上的一屏。三层运行时共用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ailesson.contract.episode import Episode


@dataclass
class Card:
    """一张卡 = 屏幕上的一屏。"""

    card_id: str
    segment_index: int
    kind: str                    # a2i / i2a / shadow / chunk / sentence / passive / assess / report
    domain: str                  # words / chunks / sentences
    item_id: str
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "segment_index": self.segment_index,
            "kind": self.kind,
            "domain": self.domain,
            "item_id": self.item_id,
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


def _choices_for_word(ep: Episode, lemma: str, siblings: list[str]) -> tuple[str, ...]:
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
