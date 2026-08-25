"""后台前端的静态回归。

前端没有 JS 测试框架，这里退而求其次：把「每块后台要能直达」这类结构约定
钉在文件上。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "admin" / "index.html").read_text()
JS = (ROOT / "web" / "admin" / "admin.js").read_text()


class TestRouting:
    """四块要能各自直达，否则给不出可分享的链接。"""

    def test_视图进hash(self):
        assert "location.hash = view" in JS

    def test_启动读hash(self):
        assert "viewFromHash" in JS

    def test_监听前进后退(self):
        assert "hashchange" in JS

    def test_四块都注册了(self):
        for view in ("tools", "arrangement", "plan", "content"):
            assert f"'{view}'" in JS, view

    def test_非法hash退回教具页(self):
        assert "VIEW_IDS.includes(h) ? h : 'tools'" in JS


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
