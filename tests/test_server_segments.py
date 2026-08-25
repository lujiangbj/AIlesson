"""剧本与切段接口：看拆分效果。

第一步（逐字稿来源 + 切段规则）走代码，规则由人给。后台这里只读，职责是把
「切成了什么样」摊开到能判断规则对不对。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, mvp_root):
    """剧本目录指到 tmp，塞一份小剧本。"""
    import ailesson.server.deps as deps
    import ailesson.server.routers.segments as seg
    import ailesson.server.state as state
    from ailesson.server.app import app

    monkeypatch.setattr(state, "DATA", tmp_path)
    monkeypatch.setattr(state, "MVP_ROOT", mvp_root)
    monkeypatch.setattr(state, "DEFAULT_EPISODE", "peppa-s01e01")
    monkeypatch.setattr(state, "LLMClient", lambda *a, **k: object())

    parsed = tmp_path / "parsed"
    parsed.mkdir()
    monkeypatch.setattr(seg, "PARSED", parsed)
    monkeypatch.setattr(seg, "VOCAB", tmp_path / "vocab")
    monkeypatch.setattr(seg, "SEGMENTS", tmp_path / "segments")
    monkeypatch.setattr(seg, "LESSONS", tmp_path / "lessons")

    items = []
    for si, (scene, n, wlen) in enumerate([
        ("Central Perk, everyone is there", 12, 10),
        ("Monica's Apartment, later", 8, 6),
        ("The Museum, Ross at work", 6, 12),
        ("Central Perk again", 5, 8),
    ]):
        items.append({"type": "scene", "text": scene})
        for i in range(n):
            items.append({"type": "line", "speaker": f"P{si}",
                          "text": " ".join(["word"] * wlen)})
    (parsed / "0101.json").write_text(json.dumps({
        "id": "0101", "season": 1, "episode": 1, "title": "The Pilot",
        "stats": {"lines": 31, "scenes": 4, "speakers": ["P0", "P1"]},
        "items": items,
    }, ensure_ascii=False))

    deps.reset_state()
    yield TestClient(app)
    deps.reset_state()


class TestScripts:
    def test_列出剧本(self, client):
        d = client.get("/api/content/scripts").json()
        assert [s["episode_id"] for s in d["scripts"]] == ["0101"]

    def test_带每集规模(self, client):
        s = client.get("/api/content/scripts").json()["scripts"][0]
        assert s["lines"] == 31
        assert s["scenes"] == 4
        assert s["title"] == "The Pilot"

    def test_报出到了哪一步(self, client):
        """状态是按产物算的，不是记下来的。"""
        s = client.get("/api/content/scripts").json()["scripts"][0]
        assert s["has_vocab"] is False
        assert s["has_segments"] is False
        assert s["has_lesson"] is False

    def test_带当前切段规则(self, client):
        assert client.get("/api/content/scripts").json()["rule"]


class TestChunks:
    def test_列出换场切出的最小单位(self, client):
        d = client.get("/api/content/scripts/0101/chunks").json()
        assert d["n_chunks"] == 4
        assert all(c["words"] > 0 for c in d["chunks"])

    def test_给出段大小的下限(self, client):
        """最大 chunk 切不开，它就是任何一段的下限 —— 判断能不能更均看这个。"""
        d = client.get("/api/content/scripts/0101/chunks").json()
        assert d["floor_words"] == max(c["words"] for c in d["chunks"])
        assert d["floor_at"]

    def test_每个chunk带占比(self, client):
        d = client.get("/api/content/scripts/0101/chunks").json()
        assert abs(sum(c["share"] for c in d["chunks"]) - 100) < 1

    def test_剧本不存在404(self, client):
        assert client.get("/api/content/scripts/9999/chunks").status_code == 404


class TestSegments:
    def test_没落盘时现算(self, client):
        d = client.get("/api/content/scripts/0101/segments").json()
        assert d["saved"] is False
        assert d["n"] >= 1

    def test_指定段数(self, client):
        d = client.get("/api/content/scripts/0101/segments?n=2").json()
        assert d["n"] == 2
        assert d["auto"] is False

    def test_每段带起止句_便于核对切点(self, client):
        for s in client.get("/api/content/scripts/0101/segments").json()["segments"]:
            assert s["first_line"]["text"]
            assert s["last_line"]["text"]

    def test_每段带估算时长(self, client):
        d = client.get("/api/content/scripts/0101/segments").json()
        assert abs(sum(s["minutes"] for s in d["segments"])
                   - d["runtime_min"]) < 0.2

    def test_带切段规则说明(self, client):
        assert client.get("/api/content/scripts/0101/segments").json()["rule"]


class TestCompare:
    def test_并排比几种段数(self, client):
        d = client.get("/api/content/scripts/0101/compare?lo=2&hi=4").json()
        assert [o["n"] for o in d["options"]] == [2, 3, 4]

    def test_指出哪种最齐(self, client):
        d = client.get("/api/content/scripts/0101/compare?lo=2&hi=4").json()
        best = min(d["options"], key=lambda o: o["spread"])
        assert d["most_even"] == best["n"]

    def test_切不出的段数不列(self, client):
        """4 个换场切不出 9 段，不能列一个假的。"""
        d = client.get("/api/content/scripts/0101/compare?lo=8&hi=9").json()
        assert d["options"] == []


class TestSegmentLines:
    def test_给出某段全部台词(self, client):
        d = client.get("/api/content/scripts/0101/segments/1/lines").json()
        assert d["index"] == 1
        lines = [i for i in d["items"] if i["type"] == "line"]
        assert len(lines) == d["lines"]

    def test_台词带说话人和场景标记(self, client):
        d = client.get("/api/content/scripts/0101/segments/1/lines").json()
        assert any(i["type"] == "scene" for i in d["items"])
        assert all(i["speaker"] for i in d["items"] if i["type"] == "line")

    def test_各段台词加起来是全集(self, client):
        """段边界反推错位的话，这条会红。"""
        plan = client.get("/api/content/scripts/0101/segments").json()
        got = 0
        for i in range(1, plan["n"] + 1):
            d = client.get(
                f"/api/content/scripts/0101/segments/{i}/lines").json()
            got += len([x for x in d["items"] if x["type"] == "line"])
        assert got == plan["total_lines"]

    def test_段号越界404(self, client):
        assert client.get(
            "/api/content/scripts/0101/segments/99/lines").status_code == 404
