"""后台接口测试：教具表 / 编排 / 计划检查器 / 完备度矩阵。

检查器必须同时给出输入和输出 —— 只有输出的话，看到一节课 12 个点会知道
「多了」，但不知道是自评、探测阈值还是 LLM 聚类的锅。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, mvp_root):
    import ailesson.server.deps as deps
    import ailesson.server.state as state
    from ailesson.server.app import app

    monkeypatch.setattr(state, "DATA", tmp_path)
    monkeypatch.setattr(state, "MVP_ROOT", mvp_root)
    monkeypatch.setattr(state, "DEFAULT_EPISODE", "peppa-s01e01")

    class DummyLLM:
        def complete(self, *a, **k):
            raise RuntimeError("测试不该调 LLM")

        def complete_json(self, *a, **k):
            raise RuntimeError("测试不该调 LLM")

    monkeypatch.setattr(state, "LLMClient", lambda *a, **k: DummyLLM())
    deps.reset_state()
    yield TestClient(app)
    deps.reset_state()


class TestTools:
    def test_列出教具(self, client):
        d = client.get("/api/admin/tools").json()
        ids = {t["id"] for t in d["tools"]}
        assert "listen_pick_image" in ids
        assert "shadow" in ids

    def test_每件教具带中文名和素材需求(self, client):
        for t in client.get("/api/admin/tools").json()["tools"]:
            assert t["name"]
            assert "needs" in t


class TestArrangement:
    def test_返回16环节(self, client):
        d = client.get("/api/admin/arrangement").json()
        assert len(d["steps"]) == 16
        assert d["version"] >= 1

    def test_环节带教具中文名(self, client):
        for s in client.get("/api/admin/arrangement").json()["steps"]:
            assert s["tool_name"], s["index"]

    def test_status_里也报编排身份(self, client):
        """前端要知道当前跑的是哪套编排的哪一版。"""
        a = client.get("/api/status").json()["arrangement"]
        assert a["id"] and a["version"] >= 1


class TestPlanInspector:
    def test_无用户返回409(self, client):
        assert client.get("/api/admin/plan").status_code == 409

    def test_有用户但没自评时报没计划(self, client):
        client.post("/api/users", json={"name": "小明"})
        d = client.get("/api/admin/plan").json()
        assert d["has_plan"] is False
        assert d["episode"]["words"] > 0

    def test_没有该节时404(self, client):
        client.post("/api/users", json={"name": "小明"})
        assert client.get("/api/admin/plan/1/cards").status_code == 404


class TestCompleteness:
    def test_矩阵三层齐全(self, client):
        d = client.get("/api/content/completeness").json()
        assert set(d["domains"]) == {"words", "chunks", "sentences"}

    def test_peppa没有缺口(self, client):
        """Peppa 这一集素材是完整的，缺口清单该是空的。"""
        d = client.get("/api/content/completeness").json()
        assert d["blockers"] == []

    def test_默认只审编排用到的教具(self, client):
        d = client.get("/api/content/completeness").json()
        assert d["arrangement_only"] is True

    def test_可以审全部教具(self, client):
        d = client.get(
            "/api/content/completeness?arrangement_only=false").json()
        assert d["arrangement_only"] is False

    def test_素材不存在时404(self, client):
        r = client.get("/api/content/completeness?episode_id=没这一集")
        assert r.status_code == 404


class TestContentEpisodes:
    def test_列出可选素材(self, client):
        d = client.get("/api/content/episodes").json()
        assert any(e["id"] == "peppa-s01e01" for e in d["episodes"])
        assert d["current"] == "peppa-s01e01"

    def test_切换到不存在的素材404(self, client):
        assert client.post("/api/content/episodes/没这一集/select") \
            .status_code == 404
