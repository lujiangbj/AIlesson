"""掌握度与调度测试（FR-5）。

沿用 MVP 的 MASTERY_STREAK=2 和双方向 streak 模型，state 结构兼容。
"""
import json

from ailesson.progress import MASTERY_STREAK, Progress


class TestRecord:
    def test_答对累加streak(self):
        p = Progress()
        p.record("words", "puddle", "a2i", True)
        e = p.entry("words", "puddle")
        assert e.streak_a2i == 1
        assert e.seen == 1
        assert e.correct == 1

    def test_答错清零(self):
        p = Progress()
        p.record("words", "puddle", "a2i", True)
        p.record("words", "puddle", "a2i", False)
        e = p.entry("words", "puddle")
        assert e.streak_a2i == 0
        assert e.wrong == 1

    def test_两方向独立(self):
        p = Progress()
        p.record("words", "puddle", "a2i", True)
        e = p.entry("words", "puddle")
        assert e.streak_a2i == 1
        assert e.streak_i2a == 0


class TestMastery:
    def test_需两方向都达标(self):
        p = Progress()
        for _ in range(MASTERY_STREAK):
            p.record("words", "puddle", "a2i", True)
        assert not p.is_mastered("words", "puddle"), "单方向达标不算掌握"
        for _ in range(MASTERY_STREAK):
            p.record("words", "puddle", "i2a", True)
        assert p.is_mastered("words", "puddle")

    def test_答错后失去掌握(self):
        p = Progress()
        for k in ("a2i", "i2a"):
            for _ in range(MASTERY_STREAK):
                p.record("words", "puddle", k, True)
        p.record("words", "puddle", "a2i", False)
        assert not p.is_mastered("words", "puddle")

    def test_状态三态(self):
        p = Progress()
        assert p.status("words", "puddle") == "unseen"
        p.record("words", "puddle", "a2i", True)
        assert p.status("words", "puddle") == "learning"
        p.record("words", "puddle", "a2i", True)
        for _ in range(MASTERY_STREAK):
            p.record("words", "puddle", "i2a", True)
        assert p.status("words", "puddle") == "mastered"


class TestReviewPicking:
    """复习词调度：优先挑答错过的、久未复习的。"""

    def test_未学过的词不进复习池(self):
        p = Progress()
        assert p.pick_review(["a", "b"], limit=8) == []

    def test_学过未掌握的进复习池(self):
        p = Progress()
        p.record("words", "a", "a2i", True)
        assert p.pick_review(["a", "b"], limit=8) == ["a"]

    def test_已掌握的也复习但优先级低(self):
        p = Progress()
        p.record("words", "weak", "a2i", False)
        for k in ("a2i", "i2a"):
            for _ in range(MASTERY_STREAK):
                p.record("words", "strong", k, True)
        got = p.pick_review(["strong", "weak"], limit=8)
        assert got[0] == "weak", "错过的词该先复习"

    def test_限流(self):
        p = Progress()
        for i in range(20):
            p.record("words", f"w{i}", "a2i", True)
        assert len(p.pick_review([f"w{i}" for i in range(20)], limit=8)) == 8

    def test_排除指定词(self):
        """本节的重点词不该同时出现在复习池。"""
        p = Progress()
        p.record("words", "a", "a2i", True)
        p.record("words", "b", "a2i", True)
        assert p.pick_review(["a", "b"], limit=8, exclude={"a"}) == ["b"]


class TestPersistence:
    def test_往返(self):
        p = Progress()
        p.record("words", "puddle", "a2i", True)
        p.record("chunks", "wear_boots", "i2a", False)
        back = Progress.from_dict(json.loads(json.dumps(p.to_dict())))
        assert back.entry("words", "puddle").streak_a2i == 1
        assert back.entry("chunks", "wear_boots").wrong == 1

    def test_兼容MVP的state结构(self):
        """NFR-4：能吃下 illit-english-mvp 存下来的进度。"""
        legacy = {
            "progress": {
                "words": {"puddle": {"streak_a2i": 2, "streak_i2a": 1,
                                     "seen": 3, "correct": 3, "wrong": 0, "lastAt": 1}},
                "chunks": {},
                "sentences": {},
            }
        }
        p = Progress.from_dict(legacy)
        assert p.entry("words", "puddle").streak_a2i == 2

    def test_兼容更老的单streak结构(self):
        legacy = {"progress": {"words": {"puddle": {"streak": 2, "seen": 2}}}}
        p = Progress.from_dict(legacy)
        e = p.entry("words", "puddle")
        assert e.streak_a2i == 2
        assert e.streak_i2a == 0

    def test_三个域独立(self):
        p = Progress()
        p.record("words", "x", "a2i", True)
        assert p.entry("chunks", "x").seen == 0
