"""课后报告（FR-7）。

报告是给**付费的人**（家长）看的，原设计文档里所有反馈都给学习者，缺了这块 ——
它是转化点，也是语音课比刷题 App 更容易讲清价值的地方。

诚实原则：课内不产生「已掌握」（FR-5.2），所以报告里绝不出现「今天掌握了 N 个词」。
写「学了 6 个 / 一次答对 5 个 / 3 个需要下次复习」，家长反而更信。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .llm import BaseLLM, LLMError


@dataclass
class LessonReport:
    episode_title: str
    lesson_index: int
    theme: str
    points_learned: int         # 教学点总数（词 + 短语 + 句子）
    first_try_correct: int
    asked: int
    correct: int
    wrong: int
    # 三层各几个：报告不能笼统说「N 个词」，那是错的口径
    n_words: int = 0
    n_chunks: int = 0
    n_sentences: int = 0
    review_next: list[str] = field(default_factory=list)
    shadow_count: int = 0
    shadow_avg: float | None = None
    shadow_issues: list[str] = field(default_factory=list)
    blind_listen_score: int | None = None
    demoted: list[str] = field(default_factory=list)
    next_theme: str = ""

    @property
    def accuracy(self) -> float:
        return (self.correct / self.asked) if self.asked else 0.0

    def learned_desc(self) -> str:
        """按三层分别描述。笼统说「10 个词」是错的 —— 实际可能是 3 词+5 短语+2 句。"""
        bits = []
        if self.n_words:
            bits.append(f"{self.n_words} 个词")
        if self.n_chunks:
            bits.append(f"{self.n_chunks} 个短语")
        if self.n_sentences:
            bits.append(f"{self.n_sentences} 个句子")
        return " + ".join(bits) if bits else f"{self.points_learned} 个教学点"

    def narrate(self, llm: BaseLLM) -> str:
        """让 LLM 把数据写成家长看得懂的一段话。失败则退回纯文本。"""
        try:
            return llm.complete(
                self._narrate_prompt(), system=_NARRATE_SYSTEM, max_tokens=400, thinking=False
            )
        except LLMError:
            return render_report_text(self)

    def _narrate_prompt(self) -> str:
        lines = [
            f"课程：{self.episode_title} 第{self.lesson_index}节 · {self.theme}",
            f"今天学了 {self.learned_desc()}，一次就答对 {self.first_try_correct} 个",
            f"共答 {self.asked} 题，对 {self.correct} 题（正确率 {self.accuracy:.0%}）",
        ]
        if self.review_next:
            lines.append(f"下次要复习：{', '.join(self.review_next)}")
        if self.shadow_count:
            lines.append(f"跟读 {self.shadow_count} 次，平均 {self.shadow_avg:.0f} 分")
        if self.shadow_issues:
            lines.append(f"发音要注意的音：{', '.join(self.shadow_issues)}")
        if self.blind_listen_score is not None:
            lines.append(f"课末盲听原片自评：{self.blind_listen_score} 档（1=听懂一点 3=大部分）")
        if self.demoted:
            lines.append(f"自称会但实测没过的词：{', '.join(self.demoted)}")
        if self.next_theme:
            lines.append(f"下节课：{self.next_theme}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_title": self.episode_title,
            "lesson_index": self.lesson_index,
            "theme": self.theme,
            "points_learned": self.points_learned,
            "n_words": self.n_words,
            "n_chunks": self.n_chunks,
            "n_sentences": self.n_sentences,
            "first_try_correct": self.first_try_correct,
            "asked": self.asked,
            "correct": self.correct,
            "wrong": self.wrong,
            "review_next": self.review_next,
            "shadow_count": self.shadow_count,
            "shadow_avg": self.shadow_avg,
            "shadow_issues": self.shadow_issues,
            "blind_listen_score": self.blind_listen_score,
            "demoted": self.demoted,
            "next_theme": self.next_theme,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LessonReport:
        return cls(
            episode_title=d.get("episode_title", ""),
            lesson_index=d.get("lesson_index", 0),
            theme=d.get("theme", ""),
            points_learned=d.get("points_learned", d.get("words_learned", 0)),
            n_words=d.get("n_words", 0),
            n_chunks=d.get("n_chunks", 0),
            n_sentences=d.get("n_sentences", 0),
            first_try_correct=d.get("first_try_correct", 0),
            asked=d.get("asked", 0),
            correct=d.get("correct", 0),
            wrong=d.get("wrong", 0),
            review_next=list(d.get("review_next", [])),
            shadow_count=d.get("shadow_count", 0),
            shadow_avg=d.get("shadow_avg"),
            shadow_issues=list(d.get("shadow_issues", [])),
            blind_listen_score=d.get("blind_listen_score"),
            demoted=list(d.get("demoted", [])),
            next_theme=d.get("next_theme", ""),
        )


_NARRATE_SYSTEM = """你在给家长写一段孩子的英语课后小结，3~4 句话。

要求：
- 说人话，别用「掌握度」「达标率」这种黑话
- **不要说孩子「掌握」了多少词** —— 一节课只是学了一遍，掌握要靠下次复习确认，
  说掌握是不诚实的
- 有需要复习的词就直说，家长要的是真实情况不是好话
- 如果盲听自评有进步，值得点一句：孩子课末能听懂原片了
- 只输出小结正文"""


def build_report(
    *,
    episode_title: str,
    lesson_index: int,
    theme: str,
    n_words: int = 0,
    n_chunks: int = 0,
    n_sentences: int = 0,
    stats: dict[str, int],
    wrong_words: list[str] | None = None,
    shadow_scores: list[float] | None = None,
    shadow_issues: list[str] | None = None,
    blind_listen_score: int | None = None,
    demoted: list[str] | None = None,
    next_theme: str = "",
) -> LessonReport:
    scores = list(shadow_scores or [])
    return LessonReport(
        episode_title=episode_title,
        lesson_index=lesson_index,
        theme=theme,
        points_learned=n_words + n_chunks + n_sentences,
        n_words=n_words,
        n_chunks=n_chunks,
        n_sentences=n_sentences,
        first_try_correct=stats.get("first_try_correct", 0),
        asked=stats.get("asked", 0),
        correct=stats.get("correct", 0),
        wrong=stats.get("wrong", 0),
        review_next=list(wrong_words or []),
        shadow_count=len(scores),
        shadow_avg=(sum(scores) / len(scores)) if scores else None,
        shadow_issues=list(shadow_issues or []),
        blind_listen_score=blind_listen_score,
        demoted=list(demoted or []),
        next_theme=next_theme,
    )


_BLIND_LABEL = {1: "听懂一点点", 2: "听懂一半", 3: "大部分听懂"}


def render_report_text(r: LessonReport) -> str:
    """纯文本报告，也是 LLM 失败时的兜底。"""
    lines = [
        f"{r.episode_title} · 第{r.lesson_index}节 · {r.theme}",
        f"今天学了 {r.learned_desc()} · 一次答对 {r.first_try_correct} 个"
        f" · 共 {r.asked} 题正确率 {r.accuracy:.0%}",
    ]
    if r.review_next:
        # 一行挤 12 项读不了，逐行列出；超过 8 项只显示前 8 个
        head = r.review_next[:8]
        more = len(r.review_next) - len(head)
        lines.append("下次要复习：")
        lines += [f"  · {x}" for x in head]
        if more:
            lines.append(f"  …还有 {more} 个")
    if r.shadow_count and r.shadow_avg is not None:
        line = f"跟读 {r.shadow_count} 次 · 平均 {r.shadow_avg:.0f} 分"
        if r.shadow_issues:
            line += f" · 注意 {' '.join(r.shadow_issues)} 音"
        lines.append(line)
    if r.blind_listen_score is not None:
        label = _BLIND_LABEL.get(r.blind_listen_score, str(r.blind_listen_score))
        lines.append(f"课末盲听原片：{label}")
    if r.demoted:
        lines.append(f"以为会其实还没过：{'、'.join(r.demoted[:5])}")
    if r.next_theme:
        lines.append(f"下节课：{r.next_theme}")
    return "\n".join(lines)
