"""语音层（FR-6）。

语音是**旁路**：可以慢、可以失败、可以被静音，但绝不能阻塞答题主路径。
点选答题在本地判定、零延迟；Tutor 的话藏在「学生看正确答案」的 1~2s 操作间隙里。

Tutor 闭嘴规则（FR-6.1）是这层最重要的东西。每题都说两句，学生第五题就想关声音 ——
这是 AI 教育产品最常见的坑，所以写成硬规则而不是靠 prompt 自觉。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .llm import BaseLLM, LLMError

PRAISE_EVERY = 3          # 连对几次夸一句


class VoiceEvent(Enum):
    SEGMENT_START = "segment_start"
    CORRECT = "correct"
    WRONG = "wrong"
    STUCK = "stuck"
    SHADOW_GOOD = "shadow_good"
    SHADOW_RETRY = "shadow_retry"
    REPORT = "report"


# 预合成语音池：高频套路语不烧 LLM，延迟≈0
CANNED: dict[VoiceEvent, list[str]] = {
    VoiceEvent.SEGMENT_START: [
        "接下来我们{title}。",
        "好，进入{title}。",
    ],
    VoiceEvent.CORRECT: [
        "Nice! 连着对了好几个。",
        "很稳，继续。",
        "Good job! 这几个都对了。",
        "厉害，一路对下来了。",
    ],
    VoiceEvent.STUCK: [
        "不着急，我帮你去掉两个。",
        "再听一遍，从这两个里挑。",
    ],
    VoiceEvent.SHADOW_GOOD: [
        "很像！",
        "这个音发得不错。",
        "对了，就是这样。",
    ],
    VoiceEvent.SHADOW_RETRY: [
        "再来一次。",
        "差一点，再试试。",
    ],
}


def should_speak(event: VoiceEvent, streak: int = 0) -> bool:
    """FR-6.1 硬规则：答错一定说，环节切换一定说，连对基本不说。"""
    if event in (VoiceEvent.WRONG, VoiceEvent.SEGMENT_START, VoiceEvent.STUCK,
                 VoiceEvent.REPORT):
        return True
    if event is VoiceEvent.CORRECT:
        # 连对到 3 的倍数才夸一次，其余闭嘴
        return streak > 0 and streak % PRAISE_EVERY == 0
    if event in (VoiceEvent.SHADOW_GOOD, VoiceEvent.SHADOW_RETRY):
        return True
    return False


class TTS(Protocol):
    def speak(self, text: str) -> bytes: ...


@dataclass
class VoiceQueue:
    """播放队列：同一时刻只准一个声音（FR-6.3）。"""

    tts: TTS
    muted: bool = False
    pending: list[str] = field(default_factory=list)
    errors: int = 0

    def push(self, text: str) -> None:
        if self.muted or not text or not text.strip():
            return
        self.pending.append(text)

    def interrupt(self) -> None:
        """丢弃还没播的 —— 学生已经往下走了，旧评语没意义。"""
        self.pending.clear()

    def drain(self) -> None:
        """把队列播完。TTS 失败只记数，不抛出（FR-6.4）。"""
        while self.pending:
            text = self.pending.pop(0)
            try:
                self.tts.speak(text)
            except Exception:
                self.errors += 1


@dataclass
class StuckHint:
    narrowed: list[str]
    line: str


# 答错时的讲解。核心是讲**意思和用法**，不是讲发音——
# "这个词读 xxx" 对学习者毫无价值，他听清了但不知道什么意思
_EXPLAIN_SYSTEM = """你是英语陪练老师，学生是中文母语的成人，正在看美剧学英语。
他刚做错一道听辨题。

用一到两句中文讲清楚，必须包含：
1. **正确答案的意思**——这是最重要的，学生现在最缺的就是这个
2. 他选错的那个是什么意思，两者差在哪
3. 如果有语境例句，点一下这个词在剧里怎么用的

硬规则：
- 不要只讲发音。"这个词读 xxx" 是废话，他已经听到音了，缺的是意思
- 说中文为主，英文词穿插着用
- 40 个汉字以内，直接说，别铺垫
- 不要说教，不要"记住哦""加油"这类话

只输出要说的话。"""

# 答对时的讲解。答对可能是蒙的，而且这是补充用法的好时机
_CONFIRM_SYSTEM = """你是英语陪练老师，学生是中文母语的成人，正在看美剧学英语。
他刚做对一道听辨题。

用一句中文确认并补充，必须包含：
1. **这个词的意思**——他可能是蒙对的，一定要确认一遍
2. 一个能用上的信息：常见搭配、语体（口语/粗俗/正式）、或剧里的用法

硬规则：
- 不要只讲发音，也不要单纯夸奖（"很好""厉害"是废话）
- 说中文为主，英文词穿插着用
- 30 个汉字以内
- 不要说教

只输出要说的话。"""


@dataclass
class TutorVoice:
    """Tutor 的嘴。事件驱动，规则决定说不说，LLM 只决定怎么说。"""

    llm: BaseLLM
    tts: TTS
    muted: bool = False
    queue: VoiceQueue = field(init=False)

    def __post_init__(self) -> None:
        self.queue = VoiceQueue(self.tts, muted=self.muted)

    def on_segment_start(self, index: int, title: str) -> None:
        if not should_speak(VoiceEvent.SEGMENT_START):
            return
        line = random.choice(CANNED[VoiceEvent.SEGMENT_START]).format(title=title)
        self.queue.push(line)

    def on_correct(self, streak: int) -> None:
        if not should_speak(VoiceEvent.CORRECT, streak=streak):
            return
        self.queue.push(random.choice(CANNED[VoiceEvent.CORRECT]))

    def on_wrong(
        self,
        target: str,
        chosen: str,
        meaning_zh: str = "",
        chosen_zh: str = "",
        example: str = "",
    ) -> str:
        """答错讲解 —— 这是刷题 App 结构上做不到的事，也是「课」的感觉来源。

        走 LLM 因为要针对「他选了什么」讲；关思考因为要藏在操作间隙里。

        chosen_zh 必须传：早先只给正确答案的中文，模型不知道学生选的是
        什么意思，讲不出"两者差在哪"，只能退化成念发音。
        """
        if not should_speak(VoiceEvent.WRONG):
            return ""
        bits = [f"正确答案：{target}（{meaning_zh or '释义缺失'}）",
                f"学生选的：{chosen}（{chosen_zh or '释义缺失'}）"]
        if example:
            bits.append(f"剧中原句：{example}")
        try:
            line = self.llm.complete(
                "\n".join(bits) + "\n请讲清正确答案的意思，以及和他选的那个差在哪。",
                system=_EXPLAIN_SYSTEM, max_tokens=300, thinking=False,
            )
        except LLMError:
            # 反挫败：模型挂了也要有反馈，用素材里的中文释义兜底。
            # 兜底也要给意思，不能只念词
            line = (f"{target} 是「{meaning_zh}」，你选的 {chosen} 是「{chosen_zh}」。"
                    if meaning_zh and chosen_zh
                    else f"{target}，{meaning_zh}。" if meaning_zh else f"是 {target}。")
        # 必须返回文本：muted 时 queue.push 会直接丢弃，只塞队列的话
        # 调用方拿不到讲解，会退回自己的兜底句（"single，单身的。"）
        self.queue.push(line)
        return line

    def on_confirm(self, target: str, meaning_zh: str = "",
                   example: str = "") -> str:
        """答对后的确认与补充。

        答对可能是蒙的，所以要复述一遍意思；同时补搭配或语体，
        让答对的题也有信息增量。返回讲解文本（前端展示用）。
        """
        bits = [f"他答对了：{target}（{meaning_zh or '释义缺失'}）"]
        if example:
            bits.append(f"剧中原句：{example}")
        try:
            return self.llm.complete(
                "\n".join(bits) + "\n确认一下意思，并补一个能用上的信息。",
                system=_CONFIRM_SYSTEM, max_tokens=200, thinking=False,
            )
        except LLMError:
            return f"{target}，{meaning_zh}。" if meaning_zh else target

    def on_stuck(self, target: str, choices: list[str]) -> StuckHint:
        """3s 不动 → 收窄到 2 选 1。

        distractors 本来就是难度合适的干扰项，正好拿来当「留哪个」。
        """
        others = [c for c in choices if c != target]
        keep = [target, others[0]] if others else [target]
        random.shuffle(keep)
        line = random.choice(CANNED[VoiceEvent.STUCK])
        self.queue.push(line)
        return StuckHint(narrowed=keep, line=line)

    def on_shadow(self, score: float, bad_phoneme: str = "") -> None:
        """跟读反馈：本地评分 + 预合成池，300ms 内。不走 LLM。"""
        if score >= 85:
            self.queue.push(random.choice(CANNED[VoiceEvent.SHADOW_GOOD]))
        else:
            line = random.choice(CANNED[VoiceEvent.SHADOW_RETRY])
            if bad_phoneme:
                line += f" 注意 {bad_phoneme} 这个音。"
            self.queue.push(line)

    def set_muted(self, muted: bool) -> None:
        """AC-8：静音后课程完整走完，不影响任何判定。"""
        self.muted = muted
        self.queue.muted = muted
        if muted:
            self.queue.interrupt()
