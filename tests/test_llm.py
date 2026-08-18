"""LLM 客户端测试。

结构与约束用 fake 断言；真实调用打 @pytest.mark.llm，默认跳过（见 pyproject addopts）。
"""
import json

import pytest

from ailesson.llm import FakeLLM, LLMClient, LLMError


class TestFakeLLM:
    """FakeLLM 是所有上层测试的注入点：可控、零成本、可断言调用参数。"""

    def test_按序返回预设回复(self):
        llm = FakeLLM(["first", "second"])
        assert llm.complete("p1") == "first"
        assert llm.complete("p2") == "second"

    def test_记录调用(self):
        llm = FakeLLM(["ok"])
        llm.complete("hello", system="sys", max_tokens=100)
        assert len(llm.calls) == 1
        assert llm.calls[0]["prompt"] == "hello"
        assert llm.calls[0]["system"] == "sys"

    def test_回复用尽报错(self):
        llm = FakeLLM(["only"])
        llm.complete("p")
        with pytest.raises(LLMError, match="预设回复已用尽"):
            llm.complete("p")

    def test_complete_json_解析(self):
        llm = FakeLLM([json.dumps({"a": 1})])
        assert llm.complete_json("p") == {"a": 1}

    def test_complete_json_容忍markdown围栏(self):
        """真实模型经常裹 ```json 围栏，必须容忍。"""
        llm = FakeLLM(['```json\n{"a": 1}\n```'])
        assert llm.complete_json("p") == {"a": 1}

    def test_complete_json_容忍前后废话(self):
        llm = FakeLLM(['好的，结果如下：\n{"a": [1, 2]}\n希望有帮助'])
        assert llm.complete_json("p") == {"a": [1, 2]}

    def test_complete_json_非法内容报错(self):
        llm = FakeLLM(["这不是 json"])
        with pytest.raises(LLMError, match="JSON"):
            llm.complete_json("p")


class TestRealClientConfig:
    def test_从pi读凭证_默认zgy(self):
        """FR-5：用 zgy provider，不用 zgy-ds 付费 key。"""
        c = LLMClient()
        assert c.provider == "zgy"
        assert c.api_key.startswith("sk-")
        assert "model.zhenguanyu.com" in c.base_url

    def test_模型id用点号(self):
        """踩坑：网关模型 ID 是 anthropic-claude-sonnet-4.6，不是 -4-6。"""
        c = LLMClient()
        assert "." in c.model


@pytest.mark.llm
class TestRealCall:
    def test_真实调用(self):
        c = LLMClient()
        out = c.complete("Reply with the single word: pong", max_tokens=20)
        assert "pong" in out.lower()

    def test_真实调用_json(self):
        c = LLMClient()
        out = c.complete_json(
            'Return JSON only: {"ok": true}', max_tokens=50
        )
        assert out["ok"] is True
