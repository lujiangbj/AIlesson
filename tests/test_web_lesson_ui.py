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

    def test_教室框架与内容分离(self):
        # 卡型渲染走注册表：加新卡型 = 注册一条，shell（顶栏/进度/dock）不动
        assert "const CARDS = {" in JS
        assert "CARDS[c.kind]" in JS
        # 框架三区恒定：顶栏 / 进度条 / 底部 dock（反馈与主行动按钮的家）
        assert "cls-top" in JS and "cls-progress" in JS and "cls-dock" in JS
        assert ".cls-dock" in HTML

    def test_环节状态机不铺给学生(self):
        # 16 环节是 lesson3 状态机的内部结构，不做成药丸轨铺在 UI 上；
        # 学生只看「环节名 + 卡片进度条」
        assert "seg-rail" not in JS
        assert "seg-pill" not in HTML

    def test_常驻静音开关(self):
        assert "toggleMute" in JS
        assert "au.muted" in JS

    def test_finished精简载荷不会把课堂渲染炸掉(self):
        # /api/lesson/current 在结束态只返回 {"finished": true}，
        # 没有 stats/segment/total；顶栏和进度轨必须能容忍。
        assert "c.stats || { correct: 0" in JS
        assert "c.segment || { index: 16" in JS
        assert "c.total || cursor + 1" in JS

    def test_进度条容忍缺失total(self):
        assert "c.total || cursor + 1" in JS

    def test_页签标题跟随当前素材且不请求_favicon(self):
        assert "document.title = 'AIlesson · ' + S.status.episode.title" in JS
        assert 'rel="icon"' in HTML


class TestCardInteraction:
    """上课卡片的交互回归。

    实测抓到的两个 bug：
    - 听音选图（a2i）作答前左栏就放着正确答案的图 —— 等于剧透
    - 看图选音（i2a）点选项立即作答，只能听到自己选的那个 —— 没法先试听
    """

    def test_a2i作答前不展示答案图(self):
        # prompt 图只在 i2a / 作答后 / 非词层（场景图不是答案）才显示
        assert "showImage = c.image && (isI2A || answered" in JS
        assert "promptCard(c, isI2A, answered)" in JS

    def test_i2a试听与作答分离(self):
        # 选项里要有独立的试听按钮，且不触发作答
        assert "class: 'opt-play'" in JS
        assert "stopPropagation" in JS
        assert ".opt-play" in HTML
        assert "先点 🔊 试听选项，再点选项作答" in JS

    def test_i2a点选项不再自动播放(self):
        assert "if (isI2A && !answered) play(ch.audio)" not in JS
        assert "if (!answered) pick(ch.id, c);" in JS

    def test_慢速音频缺失时不显示慢速按钮(self):
        # Friends 词卡 audio_slow 与常速同文件，🐢 按钮是假的
        assert "c.prompt_audio_slow !== c.prompt_audio" in JS

    def test_选项图片完整展示不裁切(self):
        # 选项格在横屏课堂里会被拉成宽矩形，cover 会把方形图裁掉上下边
        assert ".opt img { width: 100%; height: 100%; object-fit: contain; }" in HTML


class TestHomeView:
    def test_勾选结果表不残留已删除的DOMAINS引用(self):
        # 三层改听力探测时 DOMAINS 常量已删，viewHome 的勾选结果表曾残留
        # DOMAINS.map —— 已勾选用户进首页直接 ReferenceError 白屏。
        assert "DOMAINS" not in JS
        assert "['words', '词'], ['chunks', '短语'], ['sentences', '句子']" in JS
