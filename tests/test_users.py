"""用户系统测试。

MVP 阶段无密码：几个人测试，只为分用户存数据。

关键设计：**LLM 缓存不分用户**。词表分组全集共享；打包结果按待学池哈希，
两个人勾出同样的池子可以复用。分用户会白烧钱。只有学习状态分用户。
"""
import json

import pytest

from ailesson.users import User, UserStore


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path)


class TestCreate:
    def test_创建用户(self, store):
        u = store.create("小明")
        assert isinstance(u, User)
        assert u.name == "小明"
        assert u.id
        assert u.created_at > 0

    def test_id唯一(self, store):
        a = store.create("小明")
        b = store.create("小红")
        assert a.id != b.id

    def test_同名允许但id不同(self, store):
        """MVP 不做唯一性校验，同名是用户自己的事。"""
        a = store.create("小明")
        b = store.create("小明")
        assert a.id != b.id
        assert len(store.list()) == 2

    def test_id安全可做目录名(self, store):
        """名字含斜杠/空格也不能污染路径。"""
        u = store.create("../../etc/passwd 危险")
        assert "/" not in u.id
        assert ".." not in u.id
        assert u.name == "../../etc/passwd 危险"   # 显示名保留原样

    def test_名字不能为空(self, store):
        with pytest.raises(ValueError):
            store.create("   ")

    def test_创建即落盘(self, store, tmp_path):
        store.create("小明")
        assert (tmp_path / "users.json").exists()
        again = UserStore(tmp_path)
        assert len(again.list()) == 1


class TestList:
    def test_空(self, store):
        assert store.list() == []

    def test_按创建顺序(self, store):
        for n in ("A", "B", "C"):
            store.create(n)
        assert [u.name for u in store.list()] == ["A", "B", "C"]

    def test_get(self, store):
        u = store.create("小明")
        assert store.get(u.id).name == "小明"
        assert store.get("nonexistent") is None


class TestSelect:
    def test_首个用户自动选中(self, store):
        u = store.create("小明")
        assert store.current_id == u.id

    def test_后续用户不自动切换(self, store):
        a = store.create("小明")
        store.create("小红")
        assert store.current_id == a.id

    def test_切换(self, store):
        store.create("小明")
        b = store.create("小红")
        store.select(b.id)
        assert store.current_id == b.id
        assert store.current().name == "小红"

    def test_切换不存在的用户报错(self, store):
        with pytest.raises(KeyError):
            store.select("nope")

    def test_选择持久化(self, store, tmp_path):
        store.create("小明")
        b = store.create("小红")
        store.select(b.id)
        assert UserStore(tmp_path).current_id == b.id

    def test_无用户时current为None(self, store):
        assert store.current() is None
        assert store.current_id is None


class TestDelete:
    def test_删除(self, store):
        a = store.create("小明")
        store.create("小红")
        store.delete(a.id)
        assert len(store.list()) == 1
        assert store.get(a.id) is None

    def test_删除会连数据一起删(self, store):
        u = store.create("小明")
        p = store.state_path(u.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"x":1}')
        store.delete(u.id)
        assert not p.exists()
        assert not p.parent.exists()

    def test_删除当前用户后自动切到别人(self, store):
        a = store.create("小明")
        b = store.create("小红")
        store.select(a.id)
        store.delete(a.id)
        assert store.current_id == b.id

    def test_删除最后一个用户后current为None(self, store):
        u = store.create("小明")
        store.delete(u.id)
        assert store.current_id is None
        assert store.list() == []

    def test_删除不存在的报错(self, store):
        with pytest.raises(KeyError):
            store.delete("nope")


class TestIsolation:
    """核心目的：分用户存数据，互不覆盖。"""

    def test_状态路径按用户分开(self, store):
        a = store.create("小明")
        b = store.create("小红")
        assert store.state_path(a.id) != store.state_path(b.id)
        assert a.id in str(store.state_path(a.id))

    def test_写入互不影响(self, store):
        a = store.create("小明")
        b = store.create("小红")
        for u, val in ((a, "A的数据"), (b, "B的数据")):
            p = store.state_path(u.id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"who": val}, ensure_ascii=False))
        assert json.loads(store.state_path(a.id).read_text())["who"] == "A的数据"
        assert json.loads(store.state_path(b.id).read_text())["who"] == "B的数据"

    def test_LLM缓存不分用户(self, store, tmp_path):
        """缓存全局共享，别白烧钱。"""
        a = store.create("小明")
        assert store.cache_dir() == tmp_path / "cache"
        assert a.id not in str(store.cache_dir())


class TestHistory:
    """课堂数据记录：为后续调整教学留依据。"""

    def test_追加一节课记录(self, store):
        u = store.create("小明")
        store.append_history(u.id, {"lesson_index": 1, "theme": "泥水洼",
                                    "asked": 40, "correct": 35})
        h = store.history(u.id)
        assert len(h) == 1
        assert h[0]["theme"] == "泥水洼"
        assert h[0]["at"] > 0, "要自动打时间戳"

    def test_多节按时间追加(self, store):
        u = store.create("小明")
        for i in (1, 2, 3):
            store.append_history(u.id, {"lesson_index": i})
        assert [x["lesson_index"] for x in store.history(u.id)] == [1, 2, 3]

    def test_历史分用户(self, store):
        a = store.create("小明")
        b = store.create("小红")
        store.append_history(a.id, {"lesson_index": 1})
        assert len(store.history(a.id)) == 1
        assert store.history(b.id) == []

    def test_删用户连历史一起删(self, store):
        u = store.create("小明")
        store.append_history(u.id, {"lesson_index": 1})
        store.delete(u.id)
        assert store.history(u.id) == []

    def test_无历史返回空(self, store):
        u = store.create("小明")
        assert store.history(u.id) == []


class TestMigration:
    def test_迁移旧的单用户state(self, tmp_path):
        """老的 data/state.json 不能丢，自动收进第一个用户。"""
        (tmp_path / "state.json").write_text(
            json.dumps({"episode_id": "peppa-s01e01", "completed_lessons": [1, 2]})
        )
        store = UserStore(tmp_path)
        users = store.list()
        assert len(users) == 1
        snap = json.loads(store.state_path(users[0].id).read_text())
        assert snap["completed_lessons"] == [1, 2]
        assert not (tmp_path / "state.json").exists(), "迁移后应移走，避免重复迁移"

    def test_无旧数据不建用户(self, tmp_path):
        store = UserStore(tmp_path)
        assert store.list() == []

    def test_坏的旧state不炸(self, tmp_path):
        (tmp_path / "state.json").write_text("这不是 json")
        store = UserStore(tmp_path)
        assert store.list() == []


class TestTouch:
    def test_记录最近活跃(self, store):
        u = store.create("小明")
        store.touch(u.id)
        assert store.get(u.id).last_active > 0

    def test_持久化(self, store, tmp_path):
        u = store.create("小明")
        store.touch(u.id)
        assert UserStore(tmp_path).get(u.id).last_active > 0
