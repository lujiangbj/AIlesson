"""课后报告测试（FR-7 / AC-9）。

报告是给付费的人看的（家长），不是给学习者看的 —— 这是原设计文档里缺的东西。
所以数据必须诚实：课内不产生掌握，报告就不能写「今天掌握了 6 个词」。
"""
import pytest

from ailesson.llm import FakeLLM
from ailesson.report import LessonReport, build_report, render_report_text


@pytest.fixture
def data():
    return dict(
        episode_title="Muddy Puddles",
        lesson_index=3,
        theme="穿上雨靴跳泥水洼",
        n_words=3, n_chunks=5, n_sentences=2,
        stats={"asked": 60, "correct": 52, "wrong": 8, "first_try_correct": 5},
        wrong_words=["boot", "muddy", "play"],
        shadow_scores=[92.0, 88.0, 76.0, 95.0],
        shadow_issues=["ɹ"],
        blind_listen_score=3,
        demoted=["mummy"],
        next_theme="先检查泥坑安不安全",
    )


class TestLearnedDesc:
    """报告不能笼统说「N 个词」—— 三层混合时那是错的口径。"""

    def test_三层分别描述(self, data):
        r = build_report(**data)
        assert r.learned_desc() == "3 个词 + 5 个短语 + 2 个句子"

    def test_只有短语句子时不提词(self, data):
        data.update(n_words=0, n_chunks=5, n_sentences=2)
        r = build_report(**data)
        d = r.learned_desc()
        assert "词" not in d
        assert "5 个短语" in d and "2 个句子" in d

    def test_渲染文本不说N个词(self, data):
        """这是实跑发现的 bug：3词+5短语+2句 被写成「10 个词」。"""
        data.update(n_words=3, n_chunks=5, n_sentences=2)
        text = render_report_text(build_report(**data))
        assert "10 个词" not in text
        assert "3 个词" in text and "5 个短语" in text


class TestBuild:
    def test_六项数据齐全(self, data):
        """AC-9：报告含全部 6 项。"""
        r = build_report(**data)
        assert r.points_learned == 10
        assert r.first_try_correct == 5
        assert r.review_next == ["boot", "muddy", "play"]
        assert r.shadow_count == 4
        assert r.shadow_avg == pytest.approx(87.75)
        assert r.blind_listen_score == 3
        assert r.next_theme == "先检查泥坑安不安全"

    def test_不声称已掌握(self, data):
        """FR-5.2：课内不产生掌握，报告不能吹。"""
        r = build_report(**data)
        assert not hasattr(r, "mastered")
        text = render_report_text(r)
        assert "掌握" not in text

    def test_跟读平均分保留一位(self, data):
        r = build_report(**data)
        assert isinstance(r.shadow_avg, float)

    def test_无跟读时不报分(self, data):
        data["shadow_scores"] = []
        r = build_report(**data)
        assert r.shadow_count == 0
        assert r.shadow_avg is None

    def test_打回词单独列出(self, data):
        r = build_report(**data)
        assert r.demoted == ["mummy"]

    def test_正确率(self, data):
        r = build_report(**data)
        assert r.accuracy == pytest.approx(52 / 60)

    def test_零题不除零(self, data):
        data["stats"] = {"asked": 0, "correct": 0, "wrong": 0, "first_try_correct": 0}
        r = build_report(**data)
        assert r.accuracy == 0.0


class TestRender:
    def test_纯文本渲染含关键数字(self, data):
        text = render_report_text(build_report(**data))
        assert "6" in text
        assert "boot" in text
        assert "87" in text or "88" in text

    def test_无打回词时不提(self, data):
        data["demoted"] = []
        text = render_report_text(build_report(**data))
        assert "打回" not in text

    def test_盲听自评有前后对比语(self, data):
        text = render_report_text(build_report(**data))
        assert "盲听" in text


class TestLLMNarration:
    def test_LLM生成家长文案(self, data):
        r = build_report(**data)
        llm = FakeLLM(["孩子今天学了 6 个和泥水洼有关的词，跟读发音不错。"])
        out = r.narrate(llm)
        assert "6" in out
        assert llm.calls

    def test_文案提示词含真实数据(self, data):
        r = build_report(**data)
        llm = FakeLLM(["文案"])
        r.narrate(llm)
        p = llm.calls[0]["prompt"]
        assert "boot" in p
        assert "穿上雨靴跳泥水洼" in p

    def test_LLM失败时退回纯文本(self, data):
        r = build_report(**data)
        out = r.narrate(FakeLLM([]))
        assert "6" in out          # 纯文本兜底仍含数据

    def test_提示词要求不吹掌握(self, data):
        r = build_report(**data)
        llm = FakeLLM(["文案"])
        r.narrate(llm)
        sys_prompt = llm.calls[0]["system"] or ""
        assert "掌握" in sys_prompt      # 明确告知模型不要说掌握


class TestPersistence:
    def test_往返(self, data):
        import json
        r = build_report(**data)
        back = LessonReport.from_dict(json.loads(json.dumps(r.to_dict())))
        assert back == r
