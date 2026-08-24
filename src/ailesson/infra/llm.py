"""LLM 客户端。

网关 https://model.zhenguanyu.com，凭证从 ~/.pi/agent/auth.json 读。
用 zgy provider（Claude 系列）；zgy-ds 是付费 key，此处不用。

踩坑（来自 pi 配置经验）：
- 网关模型 ID 用点号：anthropic-claude-sonnet-4.6
- Anthropic 路径只认 x-api-key，Bearer 报 401
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_PROVIDER = "zgy"
DEFAULT_MODEL = "anthropic-claude-sonnet-4.6"
DEFAULT_BASE_URL = "https://model.zhenguanyu.com"
AUTH_PATH = Path("~/.pi/agent/auth.json").expanduser()


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON。

    模型爱裹 ```json 围栏、爱在前后加废话，都得容忍。

    还要剥掉 <thinking> 块：开思考时模型可能把推演过程写进正文。
    实测集级打包（157 个教学点）产出 44k 字符全是 thinking，
    一个 JSON 都没有——这种情况剥完仍然抠不出东西，会正常抛错，
    但至少半途而废的（thinking 后跟着 JSON）能救回来。
    """
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.S | re.I)
    # 未闭合的 thinking：从标签处截断，后面若有 JSON 仍可解析
    text = re.sub(r"<thinking>.*", "", text, flags=re.S | re.I)

    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退一步：抓第一个完整的 {...} 或 [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"模型输出不是合法 JSON: {text[:200]!r}")


class BaseLLM:
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        thinking: bool = False,
    ) -> str:
        raise NotImplementedError

    def complete_json(self, prompt: str, **kw: Any) -> Any:
        return _extract_json(self.complete(prompt, **kw))


class LLMClient(BaseLLM):
    def __init__(
        self,
        provider: str = DEFAULT_PROVIDER,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        # 集级打包是最重的调用：151 个教学点 + thinking 开着会跑几分钟。
        # 实测 180s 会 "read operation timed out"，静默退回机械划分
        timeout: int = 600,
    ) -> None:
        self.provider = provider
        self.model = os.environ.get("AILESSON_MODEL", model)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = self._load_key(provider)

    @staticmethod
    def _load_key(provider: str) -> str:
        env = os.environ.get("AILESSON_LLM_KEY")
        if env:
            return env
        if not AUTH_PATH.exists():
            raise LLMError(f"找不到凭证文件 {AUTH_PATH}，或设置 AILESSON_LLM_KEY")
        auth = json.loads(AUTH_PATH.read_text())
        if provider not in auth:
            raise LLMError(f"{AUTH_PATH} 里没有 provider {provider!r}")
        return auth[provider]["key"]

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        thinking: bool = False,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        if thinking:
            # 高档位任务（分诊排序 / 打包）值得开思考
            body["thinking"] = {"type": "enabled", "budget_tokens": max(1024, max_tokens // 2)}

        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(body).encode(),
            headers={
                "x-api-key": self.api_key,  # 网关 Anthropic 路径只认这个
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise LLMError(f"LLM HTTP {e.code}: {e.read()[:500]!r}") from e
        except Exception as e:
            raise LLMError(f"LLM 调用失败: {e}") from e

        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        out = "".join(parts).strip()
        if not out:
            raise LLMError(f"LLM 返回空内容: {str(data)[:300]}")
        return out


class FakeLLM(BaseLLM):
    """测试替身：按序返回预设回复，并记录调用参数。"""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        thinking: bool = False,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "max_tokens": max_tokens,
                "thinking": thinking,
            }
        )
        if not self.replies:
            raise LLMError("FakeLLM 预设回复已用尽")
        return self.replies.pop(0)
