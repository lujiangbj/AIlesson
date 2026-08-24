"""编排换了，旧快照不许重建 —— §10.5 的升级版。

卡序是确定性重建的：快照只存重建输入，牌靠 _build_cards 重算。前提是编排恒定。
编排一旦可编辑，上周的快照拿这周改过的编排去 restore 会重建出另一副牌 ——
续上错位，而且**不会报错**。这类 bug 只有端到端才暴露，钉死在这里。
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from ailesson.classroom.arrangement import DEFAULT
from ailesson.contract.lesson_spec import CoursePlan, LessonSpec


class DummyLLM:
    def complete(self, *a, **k):
        raise RuntimeError("测试不该调 LLM")

    def complete_json(self, *a, **k):
        raise RuntimeError("测试不该调 LLM")


@pytest.fixture
def spec():
    return LessonSpec(
        episode_id="peppa-s01e01", index=1, theme="在泥水洼里跳要穿雨靴",
        focus_words=["puddle", "muddy"],
        chunk_ids=["muddy_puddles", "jump_in_puddles"],
        sentence_ids=["s07", "s08"],
    )


def _make_state(tmp_path, monkeypatch, mvp_root, arrangement):
    import ailesson.server.state as state

    monkeypatch.setattr(state, "MVP_ROOT", mvp_root)
    monkeypatch.setattr(state, "DEFAULT_EPISODE", "peppa-s01e01")
    return state.AppState(
        data_dir=tmp_path,
        repo=state.ContentRepo(mvp_root=mvp_root),
        llm=DummyLLM(),
        arrangement=arrangement,
    )


def _seed(tmp_path, monkeypatch, mvp_root, spec, *, stamp: dict | None = None):
    """造一个「上到第 5 张卡」的存档，返回 (uid, 存档路径)。"""
    s = _make_state(tmp_path, monkeypatch, mvp_root, DEFAULT)
    u = s.users.create("小明")
    s.session.plan = CoursePlan(episode_id="peppa-s01e01", lessons=[spec])
    rt = s.session.start_lesson(1)
    for _ in range(5):
        rt.answer(True) if rt.current().needs_answer else rt.advance()
    s.runtime = rt
    s.save()

    p = s.learner.state_path(u.id)
    if stamp is not None:
        snap = json.loads(p.read_text())
        snap["lesson"] = {**snap["lesson"], **stamp}
        p.write_text(json.dumps(snap, ensure_ascii=False))
    return u.id, p


class TestStamp:
    def test_快照记下编排身份(self, tmp_path, monkeypatch, mvp_root, spec):
        _, p = _seed(tmp_path, monkeypatch, mvp_root, spec)
        ls = json.loads(p.read_text())["lesson"]
        assert ls["arrangement_id"] == DEFAULT.id
        assert ls["arrangement_version"] == DEFAULT.version


class TestCompatible:
    def test_同一套编排能续上(self, tmp_path, monkeypatch, mvp_root, spec):
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        s = _make_state(tmp_path, monkeypatch, mvp_root, DEFAULT)
        assert s.runtime is not None
        assert s.runtime.cursor == 5
        assert s.stale_snapshot is None

    def test_老存档没记编排也能续上(self, tmp_path, monkeypatch, mvp_root, spec):
        """v0.6 之前的快照没这两个字段，它们跑的就是默认编排。"""
        _, p = _seed(tmp_path, monkeypatch, mvp_root, spec)
        snap = json.loads(p.read_text())
        snap["lesson"].pop("arrangement_id")
        snap["lesson"].pop("arrangement_version")
        p.write_text(json.dumps(snap, ensure_ascii=False))

        s = _make_state(tmp_path, monkeypatch, mvp_root, DEFAULT)
        assert s.runtime is not None
        assert s.runtime.cursor == 5


class TestIncompatible:
    """这是本文件的重点：不匹配时必须**拒绝重建**，而不是重算出另一副牌。"""

    def test_版本升了就不重建课堂(self, tmp_path, monkeypatch, mvp_root, spec):
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        bumped = dataclasses.replace(DEFAULT, version=DEFAULT.version + 1)
        s = _make_state(tmp_path, monkeypatch, mvp_root, bumped)
        assert s.runtime is None, "编排换了还重建 = 续上会错位到别的卡"

    def test_不重建但要报出来(self, tmp_path, monkeypatch, mvp_root, spec):
        """静默丢掉进度更糟 —— 前端要能提示「这节得重开」。"""
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        bumped = dataclasses.replace(DEFAULT, version=DEFAULT.version + 1)
        s = _make_state(tmp_path, monkeypatch, mvp_root, bumped)
        assert s.stale_snapshot is not None
        assert s.stale_snapshot["lesson_index"] == 1
        assert s.stale_snapshot["cursor"] == 5
        assert s.stale_snapshot["arrangement_version"] == DEFAULT.version

    def test_换了另一套编排也不重建(self, tmp_path, monkeypatch, mvp_root, spec):
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        other = dataclasses.replace(DEFAULT, id="short-8")
        s = _make_state(tmp_path, monkeypatch, mvp_root, other)
        assert s.runtime is None
        assert s.stale_snapshot["arrangement_id"] == DEFAULT.id

    def test_课程表和进度不受影响(self, tmp_path, monkeypatch, mvp_root, spec):
        """只作废当前那节课堂，已完成的课和掌握度都还在。"""
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        bumped = dataclasses.replace(DEFAULT, version=DEFAULT.version + 1)
        s = _make_state(tmp_path, monkeypatch, mvp_root, bumped)
        assert s.session.plan is not None
        assert len(s.session.plan.lessons) == 1
        assert s.session.progress.entry("words", "puddle").seen > 0

    def test_重开后能正常上课(self, tmp_path, monkeypatch, mvp_root, spec):
        """作废之后必须还能重开这一节，不能卡死。"""
        _seed(tmp_path, monkeypatch, mvp_root, spec)
        bumped = dataclasses.replace(DEFAULT, version=DEFAULT.version + 1)
        s = _make_state(tmp_path, monkeypatch, mvp_root, bumped)
        rt = s.session.start_lesson(1)
        assert rt is not None
        assert rt.cursor == 0
        assert rt.arrangement.version == bumped.version
