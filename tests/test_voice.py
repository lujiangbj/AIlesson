"""语音层测试（FR-6 / AC-8）。

核心约束：语音是旁路。它可以慢、可以失败、可以被静音，但绝不能阻塞答题。
"""
import pytest

from ailesson.llm import FakeLLM
from ailesson.voice import (
    CANNED,
    TutorVoice,
    VoiceEvent,
    VoiceQueue,
    should_speak,
)


class FakeTTS:
    def __init__(self, fail: bool = False):
        self.spoken: list[str] = []
        self.fail = fail

    def speak(self, text: str) -> bytes:
        if self.fail:
            raise RuntimeError("TTS 挂了")
        self.spoken.append(text)
        return b"audio"


class TestShouldSpeak:
    """FR-6.1 Tutor 闭嘴规则 —— 每题都说话是最常见的 AI 教育产品坑。"""

    def test_答错一定说(self):
        assert should_speak(VoiceEvent.WRONG, streak=0)

    def test_环节切换一定说(self):
        assert should_speak(VoiceEvent.SEGMENT_START, streak=0)

    def test_连对不说话(self):
        assert not should_speak(VoiceEvent.CORRECT, streak=1)
        assert not should_speak(VoiceEvent.CORRECT, streak=2)

    def test_连对3次才夸(self):
        assert should_speak(VoiceEvent.CORRECT, streak=3)

    def test_连对6次再夸一次(self):
        assert should_speak(VoiceEvent.CORRECT, streak=6)
        assert not should_speak(VoiceEvent.CORRECT, streak=4)
        assert not should_speak(VoiceEvent.CORRECT, streak=5)

    def test_每题都说是禁止的(self):
        """连续 10 题全对，说话次数必须远小于 10。"""
        spoke = sum(1 for s in range(1, 11) if should_speak(VoiceEvent.CORRECT, streak=s))
        assert spoke <= 3, f"说了 {spoke} 次，太吵"


class TestQueue:
    """FR-6.3：同一时刻只准一个声音。"""

    def test_串行播放(self):
        tts = FakeTTS()
        q = VoiceQueue(tts)
        q.push("一")
        q.push("二")
        q.drain()
        assert tts.spoken == ["一", "二"]

    def test_静音后不播(self):
        """AC-8：静音后课程完整走完。"""
        tts = FakeTTS()
        q = VoiceQueue(tts, muted=True)
        q.push("话")
        q.drain()
        assert tts.spoken == []

    def test_TTS失败不抛出(self):
        """FR-6.4：语音故障不得阻塞主路径。"""
        q = VoiceQueue(FakeTTS(fail=True))
        q.push("话")
        q.drain()          # 不抛异常即通过
        assert q.errors == 1

    def test_打断丢弃待播(self):
        tts = FakeTTS()
        q = VoiceQueue(tts)
        q.push("旧的")
        q.interrupt()
        q.push("新的")
        q.drain()
        assert tts.spoken == ["新的"]

    def test_空文本不入队(self):
        tts = FakeTTS()
        q = VoiceQueue(tts)
        q.push("")
        q.push("   ")
        q.drain()
        assert tts.spoken == []


class TestCanned:
    """预合成语音池：高频套路语不该每次都调 LLM。"""

    def test_各事件都有预设(self):
        for ev in (VoiceEvent.SEGMENT_START, VoiceEvent.CORRECT,
                   VoiceEvent.STUCK, VoiceEvent.SHADOW_GOOD, VoiceEvent.SHADOW_RETRY):
            assert CANNED.get(ev), ev

    def test_夸奖有多条避免重复(self):
        assert len(CANNED[VoiceEvent.CORRECT]) >= 3


class TestTutorVoice:
    def test_答错走LLM讲解(self, ):
        llm = FakeLLM(["That's a boot. Listen again — puddle."])
        tts = FakeTTS()
        v = TutorVoice(llm, tts)
        v.on_wrong(target="puddle", chosen="boot", meaning_zh="水洼")
        v.queue.drain()
        assert tts.spoken
        assert "puddle" in tts.spoken[0]

    def test_讲解提示词含正确答案与误选(self):
        llm = FakeLLM(["ok"])
        v = TutorVoice(llm, FakeTTS())
        v.on_wrong(target="puddle", chosen="boot", meaning_zh="水洼")
        p = v.llm.calls[0]["prompt"]
        assert "puddle" in p and "boot" in p

    def test_讲解关思考求快(self):
        """答错讲解藏在操作间隙里，要快。"""
        llm = FakeLLM(["ok"])
        v = TutorVoice(llm, FakeTTS())
        v.on_wrong(target="puddle", chosen="boot", meaning_zh="水洼")
        assert v.llm.calls[0]["thinking"] is False

    def test_LLM失败时用中文兜底(self):
        """FR-6.4 + 反挫败：模型挂了也要有反馈。"""
        tts = FakeTTS()
        v = TutorVoice(FakeLLM([]), tts)
        v.on_wrong(target="puddle", chosen="boot", meaning_zh="水洼")
        v.queue.drain()
        assert tts.spoken
        assert "水洼" in tts.spoken[0]

    def test_连对不说话不调LLM(self):
        llm = FakeLLM(["不该被调用"])
        tts = FakeTTS()
        v = TutorVoice(llm, tts)
        v.on_correct(streak=1)
        v.queue.drain()
        assert tts.spoken == []
        assert llm.calls == []

    def test_连对3次用预设不调LLM(self):
        """夸奖走预合成池，不烧 LLM。"""
        llm = FakeLLM(["不该被调用"])
        tts = FakeTTS()
        v = TutorVoice(llm, tts)
        v.on_correct(streak=3)
        v.queue.drain()
        assert tts.spoken
        assert llm.calls == []

    def test_环节切换用预设(self):
        tts = FakeTTS()
        v = TutorVoice(FakeLLM([]), tts)
        v.on_segment_start(2, "会词抽检")
        v.queue.drain()
        assert tts.spoken

    def test_静音时完全不产生语音(self):
        llm = FakeLLM(["讲解"])
        tts = FakeTTS()
        v = TutorVoice(llm, tts, muted=True)
        v.on_wrong(target="puddle", chosen="boot", meaning_zh="水洼")
        v.on_segment_start(3, "新词首触")
        v.queue.drain()
        assert tts.spoken == []

    def test_卡住引导收窄选项(self):
        """FR-6：3s 不动 → 用 distractors 收窄到 2 选 1。"""
        v = TutorVoice(FakeLLM([]), FakeTTS())
        hint = v.on_stuck(target="puddle", choices=["puddle", "boot", "rain", "muddy"])
        assert len(hint.narrowed) == 2
        assert "puddle" in hint.narrowed
