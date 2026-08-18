"""用户系统的 API 层测试（FastAPI TestClient，不调 LLM）。"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, mvp_root):
    """把 DATA 指到 tmp，避免污染真实进度。"""
    import ailesson.server as srv

    monkeypatch.setattr(srv, "DATA", tmp_path)
    monkeypatch.setattr(srv, "MVP_ROOT", mvp_root)
    monkeypatch.setattr(srv, "store", None)          # 重置单例

    # 不让测试真的建 LLM 客户端（要读凭证）
    class DummyLLM:
        def complete(self, *a, **k):
            raise RuntimeError("测试不该调 LLM")

        def complete_json(self, *a, **k):
            raise RuntimeError("测试不该调 LLM")

    monkeypatch.setattr(srv, "LLMClient", lambda *a, **k: DummyLLM())
    return TestClient(srv.app)


class TestCrud:
    def test_初始无用户(self, client):
        r = client.get("/api/users").json()
        assert r["users"] == []
        assert r["current_id"] is None

    def test_创建(self, client):
        r = client.post("/api/users", json={"name": "小明"}).json()
        assert len(r["users"]) == 1
        assert r["users"][0]["name"] == "小明"
        assert r["current_id"] == r["users"][0]["id"], "第一个应自动选中"

    def test_空名字400(self, client):
        assert client.post("/api/users", json={"name": "  "}).status_code == 400

    def test_列表带完成课数(self, client):
        client.post("/api/users", json={"name": "小明"})
        r = client.get("/api/users").json()
        assert r["users"][0]["lessons_done"] == 0

    def test_切换(self, client):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        b = client.post("/api/users", json={"name": "B"}).json()["users"][1]["id"]
        r = client.post(f"/api/users/{b}/select").json()
        assert r["current_id"] == b
        assert "status" in r, "切换后要带上新状态，前端好一次刷新"
        assert client.post(f"/api/users/{a}/select").json()["current_id"] == a

    def test_切换不存在404(self, client):
        assert client.post("/api/users/nope/select").status_code == 404

    def test_删除(self, client):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        client.post("/api/users", json={"name": "B"})
        r = client.delete(f"/api/users/{a}").json()
        assert [u["name"] for u in r["users"]] == ["B"]
        assert r["current_id"] != a

    def test_删除最后一个后无当前用户(self, client):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        r = client.delete(f"/api/users/{a}").json()
        assert r["users"] == []
        assert r["current_id"] is None

    def test_删除不存在404(self, client):
        assert client.delete("/api/users/nope").status_code == 404


class TestGuard:
    """没有用户时，涉及学习数据的接口要明确拒绝，而不是静默写到别处。"""

    def test_无用户时checklist返回409(self, client):
        assert client.get("/api/checklist").status_code == 409

    def test_无用户时上课返回409(self, client):
        assert client.post("/api/lesson/1/start").status_code == 409

    def test_status无用户也能读(self, client):
        r = client.get("/api/status").json()
        assert r["user"] is None
        assert r["episode"]["id"] == "peppa-s01e01"

    def test_有用户后status带用户(self, client):
        client.post("/api/users", json={"name": "小明"})
        assert client.get("/api/status").json()["user"]["name"] == "小明"


class TestIsolation:
    """核心：两个用户的数据互不覆盖。"""

    def _seed(self, client, tmp_path, uid, lessons):
        """直接写 state 文件，模拟这个用户已经学了几节。"""
        import ailesson.server as srv
        p = srv.S().users.state_path(uid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "episode_id": "peppa-s01e01", "completed_lessons": lessons,
            "progress": {}, "assessment": None, "plan": None,
        }))

    def test_切换后读到各自的进度(self, client, tmp_path):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        b = client.post("/api/users", json={"name": "B"}).json()["users"][1]["id"]
        self._seed(client, tmp_path, a, [1, 2, 3])
        self._seed(client, tmp_path, b, [1])

        client.post(f"/api/users/{a}/select")
        assert client.get("/api/status").json()["completed_lessons"] == [1, 2, 3]
        client.post(f"/api/users/{b}/select")
        assert client.get("/api/status").json()["completed_lessons"] == [1]

    def test_reset只清当前用户(self, client, tmp_path):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        b = client.post("/api/users", json={"name": "B"}).json()["users"][1]["id"]
        self._seed(client, tmp_path, a, [1, 2])
        self._seed(client, tmp_path, b, [1, 2])

        client.post(f"/api/users/{a}/select")
        client.post("/api/reset")
        assert client.get("/api/status").json()["completed_lessons"] == []
        client.post(f"/api/users/{b}/select")
        assert client.get("/api/status").json()["completed_lessons"] == [1, 2], \
            "B 的进度不该被 A 的 reset 影响"

    def test_删用户不影响别人(self, client, tmp_path):
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        b = client.post("/api/users", json={"name": "B"}).json()["users"][1]["id"]
        self._seed(client, tmp_path, b, [1, 2])
        client.delete(f"/api/users/{a}")
        client.post(f"/api/users/{b}/select")
        assert client.get("/api/status").json()["completed_lessons"] == [1, 2]

    def test_空会话不覆盖已有进度(self, client, tmp_path):
        """切用户时会先保存当前会话。如果当前是刚起进程的空态，
        不能用它把盘上的进度清掉 —— 这会静默毁掉真实学习数据。"""
        a = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        b = client.post("/api/users", json={"name": "B"}).json()["users"][1]["id"]
        self._seed(client, tmp_path, a, [1, 2, 3])
        # A 是当前用户但内存里是空态，切到 B 会触发 save(A)
        client.post(f"/api/users/{b}/select")
        client.post(f"/api/users/{a}/select")
        assert client.get("/api/status").json()["completed_lessons"] == [1, 2, 3]

    def test_LLM缓存全局共享(self, client, tmp_path):
        """缓存不该跟着用户走，否则白烧钱。"""
        import ailesson.server as srv
        client.post("/api/users", json={"name": "A"})
        assert srv.S().cache.root == tmp_path / "cache"


class TestHistory:
    def test_初始为空(self, client):
        uid = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        assert client.get(f"/api/users/{uid}/history").json()["history"] == []

    def test_不存在的用户404(self, client):
        assert client.get("/api/users/nope/history").status_code == 404

    def test_记录可读回(self, client):
        import ailesson.server as srv
        uid = client.post("/api/users", json={"name": "A"}).json()["users"][0]["id"]
        srv.S().users.append_history(uid, {"lesson_index": 1, "accuracy": 0.9})
        h = client.get(f"/api/users/{uid}/history").json()["history"]
        assert len(h) == 1
        assert h[0]["accuracy"] == 0.9


class TestMigration:
    def test_旧state自动迁移(self, tmp_path, monkeypatch, mvp_root):
        import ailesson.server as srv
        (tmp_path / "state.json").write_text(
            json.dumps({"episode_id": "peppa-s01e01", "completed_lessons": [1, 2]}))
        monkeypatch.setattr(srv, "DATA", tmp_path)
        monkeypatch.setattr(srv, "MVP_ROOT", mvp_root)
        monkeypatch.setattr(srv, "store", None)
        monkeypatch.setattr(srv, "LLMClient", lambda *a, **k: object())
        c = TestClient(srv.app)
        r = c.get("/api/users").json()
        assert len(r["users"]) == 1
        assert c.get("/api/status").json()["completed_lessons"] == [1, 2]
