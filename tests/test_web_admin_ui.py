"""后台前端的静态回归。

前端没有 JS 测试框架，这里退而求其次：把「每块后台要能直达」这类结构约定
钉在文件上。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "admin" / "index.html").read_text()
JS = (ROOT / "web" / "admin" / "admin.js").read_text()


class TestNoCache:
    """前端一律不缓存：改完刷新就该看到新的。

    实测踩过：改了 admin.js、服务端也返回了新内容，页面还是旧的 ——
    浏览器对没有缓存头的响应做了启发式缓存。排查这个比多一次请求贵得多。
    """

    def test_四个前端入口都不缓存(self):
        from fastapi.testclient import TestClient

        from ailesson.server.app import app

        c = TestClient(app)
        for path in ("/", "/app.js", "/admin", "/admin/admin.js"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "no-store" in r.headers.get("cache-control", ""), path


class TestHierarchy:
    """三个后台的作用域不同，平级铺开会让层级混乱。

        教研内容  作用域 = 一部剧的一集
        课程      作用域 = 一个学习者
        系统      作用域 = 全局

    所以导航必须分两级，右上角只显示当前后台相关的那个作用域。
    """

    def test_三个后台各自成组(self):
        assert "const BACKENDS" in JS
        for bid in ("'research'", "'course'", "'system'"):
            assert bid in JS, bid

    def test_每个后台标了作用域(self):
        for scope in ("按剧集", "按学习者", "全局"):
            assert scope in JS, scope

    def test_有二级导航(self):
        assert 'getElementById(\'sub\')' in JS
        assert 'id="sub"' in HTML

    def test_只有一页时不铺二级(self):
        assert "pages.length > 1" in JS
        assert "empty-row" in JS and "#sub.empty-row" in HTML

    def test_右上角按后台显示作用域(self):
        """看剧本时不该挂着学习者和编排版本。"""
        assert "function scopeText" in JS
        assert "scopeText(cur)" in JS

    def test_页面到后台的映射是推导出来的(self):
        """别手写两份，加一页只改 BACKENDS。"""
        assert "PAGE_OF" in JS and "backendOf" in JS


class TestRouting:
    """每页要能直达，否则给不出可分享的链接。"""

    def test_视图进hash(self):
        assert "location.hash = path" in JS

    def test_hash带后台前缀(self):
        assert "`${backendOf(view)}/${view}`" in JS

    def test_启动读hash(self):
        assert "viewFromHash" in JS

    def test_监听前进后退(self):
        assert "hashchange" in JS

    def test_五个页面都注册了(self):
        for view in ("scripts", "assets", "plan", "tools", "arrangement"):
            assert f"'{view}'" in JS, view

    def test_只给后台名也能进(self):
        """#research 该进它的第一页。"""
        assert "PAGES_OF[page]" in JS

    def test_非法hash退回剧本页(self):
        assert "return 'scripts';" in JS


class TestReadOnly:
    def test_不发写请求(self):
        """后台是只读的。除了换素材，不该有 POST/DELETE。"""
        for verb in ("method: 'DELETE'", "method: 'PUT'"):
            assert verb not in JS, verb


class TestSurface:
    def test_矩阵与缺口都在(self):
        assert "缺口" in JS and "矩阵" in JS

    def test_检查器给出输入而不只是输出(self):
        # 只看输出对迭代没用，要能看到自评 / 探测 / 挑选
        for label in ("输入 · 自评", "输入 · 听力探测", "输入 · 动态挑选",
                      "输出 · 课程表"):
            assert label in JS, label

    def test_标出机械划分(self):
        assert "fallback" in JS

    def test_不露内部id(self):
        """§4：显示 labels（原文），不显示 focus_words 这类 id 列表。"""
        assert "l.labels" in JS

    def test_编排页解释版本号(self):
        assert "版本号" in JS

    def test_页面标题和四个标签(self):
        assert "AIlesson 后台" in HTML
        for label in ("教具", "编排", "课程计划", "内容完备度"):
            assert label in JS, label
