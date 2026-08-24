"""_extract_json 剥 <thinking> 块的测试。

实测集级打包时模型把推演写进正文：157 个教学点产出 44k 字符全是
<thinking>，一个 JSON 都没有，直接静默退回机械划分。
"""
import pytest

from ailesson.infra.llm import LLMError, _extract_json


class TestStripThinking:
    def test_剥掉闭合的_thinking_块(self):
        raw = '<thinking>让我想想，先数一下有几个点</thinking>{"lessons": [1]}'
        assert _extract_json(raw) == {"lessons": [1]}

    def test_剥掉未闭合的_thinking(self):
        """模型可能忘了闭标签，后面的 JSON 仍要能救回来。"""
        raw = 'preamble {"ok": 1} <thinking>接着推演但没闭合'
        assert _extract_json(raw) == {"ok": 1}

    def test_thinking_在_JSON_之后(self):
        raw = '{"a": 2}\n<thinking>回头再检查一遍</thinking>'
        assert _extract_json(raw) == {"a": 2}

    def test_大小写不敏感(self):
        raw = '<THINKING>x</THINKING>[1, 2]'
        assert _extract_json(raw) == [1, 2]

    def test_thinking_跨多行(self):
        raw = '<thinking>\n第一行\n第二行\n</thinking>\n{"n": 3}'
        assert _extract_json(raw) == {"n": 3}

    def test_全是_thinking_无_JSON_则报错(self):
        """真实故障形态：44k 字符推演、零 JSON。剥完仍抠不出就该抛错，
        让上层记日志走兜底，而不是假装成功。"""
        with pytest.raises(LLMError):
            _extract_json('<thinking>' + 'x' * 500)

    def test_thinking_里的花括号不干扰解析(self):
        """推演里常出现 {...} 示例，不能被当成正文 JSON 抠出来。"""
        raw = ('<thinking>草稿：{"lessons": ["wrong"]}</thinking>'
               '{"lessons": ["right"]}')
        assert _extract_json(raw)["lessons"] == ["right"]

    def test_与围栏共存(self):
        raw = '<thinking>推演</thinking>\n```json\n{"k": 9}\n```'
        assert _extract_json(raw) == {"k": 9}

    def test_没有_thinking_时行为不变(self):
        assert _extract_json('{"plain": true}') == {"plain": True}
