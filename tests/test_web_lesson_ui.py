"""上课页横屏课堂 UI 的静态回归测试。

前端没有 JS 测试框架，这里退而求其次：把「上课页必须走横屏课堂布局」的
关键结构钉在 web/ 两个文件上。重构后如果退回 480px 手机竖屏布局，
这些标记会先红。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text()
JS = (ROOT / "web" / "app.js").read_text()


class TestLandscapeLessonShell:
    def test_上课页使用横屏课堂外壳(self):
        assert ".lesson-stage" in HTML
        assert "lesson-stage" in JS
        assert "body.classing" in HTML
        assert "grid-template-columns" in HTML

    def test_上课时切换课堂模式(self):
        assert "classList.toggle('classing'" in JS or "classList.add('classing'" in JS

    def test_横屏提示覆盖竖屏触屏(self):
        assert "@media (orientation: portrait)" in HTML
        assert "rotate-guard" in JS or "rotate-guard" in HTML

    def test_有十六环节进度轨(self):
        assert "seg-rail" in HTML
        assert "seg-rail" in JS

    def test_常驻静音开关(self):
        assert "toggleMute" in JS
        assert "au.muted" in JS
