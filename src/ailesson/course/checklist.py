"""词表勾选式分诊（替代顺序分诊）。

为什么换掉顺序分诊：一题一题问 53 次，对已有词汇量的用户是纯浪费。铺开勾选
3 分钟就完事，而且能横向比较（"这几个我都会，那几个不认识"），判断更准。

连带砍掉的东西：
- LLM 难度排序（35s）—— 打散是为「连续 5 个不会提前终止」服务的，勾选没有终止
- 提前终止逻辑本身

保留的：按语义场分组（用户扫得快），这个仍用 LLM。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ailesson.contract.episode import Episode
from ailesson.infra.llm import BaseLLM, LLMError

GROUP_TARGET = 8          # 每组词数目标，一屏能扫完


@dataclass
class WordGroup:
    title: str
    words: list[str]


_GROUP_SYSTEM = f"""你在给一集英语动画的生词表分组，方便学习者快速勾选「哪些我已经会了」。

分组要求：
1. 按**语义场 / 剧情场景**分组，不要按词性或字母序
2. 每组 {GROUP_TARGET} 个词左右，最少 4 个，最多 12 个
3. 组名用中文，3~6 字，要具体（例："人物称呼"、"泥水洼"、"打扫清洁"）
4. 同一组的词应该让人一眼看出关联，扫一遍就能判断会不会
5. 把最基础/最高频的组放前面

只输出 JSON：
{{"groups": [{{"title": "组名", "words": ["词", ...]}}, ...]}}"""


def _fallback_groups(ep: Episode, missing: list[str]) -> list[WordGroup]:
    """按词频切块兜底。分不出语义场，但至少高频在前。"""
    ordered = sorted(missing, key=lambda w: -ep.word(w).freq)
    out: list[WordGroup] = []
    for i in range(0, len(ordered), GROUP_TARGET):
        chunk = ordered[i : i + GROUP_TARGET]
        label = "常见词" if i == 0 else f"其他词 {i // GROUP_TARGET + 1}"
        out.append(WordGroup(title=label, words=chunk))
    return out


def build_checklist(ep: Episode, llm: BaseLLM) -> list[WordGroup]:
    """把一集的词按语义场分组，供铺开勾选。"""
    valid = {w.lemma for w in ep.words}
    lines = [
        f"作品：{ep.title}（L1 学龄前动画）",
        f"共 {len(ep.words)} 个词，全部都要分进组里：",
    ]
    for w in sorted(ep.words, key=lambda w: -w.freq):
        lines.append(f"  {w.lemma}（{w.meaning_zh}，出现{w.freq}次）")

    groups: list[WordGroup] = []
    seen: set[str] = set()
    try:
        data = llm.complete_json("\n".join(lines), system=_GROUP_SYSTEM, thinking=False)
        raw = data["groups"] if isinstance(data, dict) else data
        for g in raw or []:
            if not isinstance(g, dict):
                continue
            words = [w for w in g.get("words", []) if w in valid and w not in seen]
            if not words:
                continue
            seen |= set(words)
            groups.append(WordGroup(title=str(g.get("title") or "其他"), words=words))
    except (LLMError, KeyError, TypeError):
        pass

    # 漏掉的词补进兜底组，一个都不能丢
    missing = [w.lemma for w in ep.words if w.lemma not in seen]
    if missing:
        groups += _fallback_groups(ep, missing)
    return groups


_ITEM_SYSTEM = """你在给一集影视的{label}清单分组，方便学习者勾选「哪些我已经能说出来」。

分组要求：
1. 按**场景 / 功能**分组（例："自我介绍"、"提议和请求"、"惊讶和责备"）
2. 每组 {n} 个左右，最少 3 个
3. 组名用中文，3~6 字
4. 把最简单最常用的组放前面

只输出 JSON：
{{"groups": [{{"title": "组名", "items": ["id", ...]}}, ...]}}"""


def build_item_checklist(
    ep: Episode, llm: BaseLLM, domain: str
) -> list[WordGroup]:
    """给短语或句子分组（domain = "chunks" | "sentences"）。

    句子有天然顺序（剧情），所以句子分组要保持 s01→s16 的先后。
    """
    if domain == "chunks":
        items = [(c.id, c.text, c.meaning_zh) for c in ep.chunks]
        label = "短语"
    else:
        items = [(s.id, s.text, s.meaning_zh) for s in ep.sentences]
        label = "句子"
    valid = {i for i, _, _ in items}

    lines = [f"作品：{ep.title}", f"共 {len(items)} 个{label}，全部都要分进组里："]
    for i, text, zh in items:
        lines.append(f'  {i}  "{text}"（{zh}）')

    groups: list[WordGroup] = []
    seen: set[str] = set()
    try:
        data = llm.complete_json(
            "\n".join(lines),
            system=_ITEM_SYSTEM.format(label=label, n=GROUP_TARGET),
            thinking=False,
        )
        raw = data["groups"] if isinstance(data, dict) else data
        for g in raw or []:
            if not isinstance(g, dict):
                continue
            ids = [x for x in (g.get("items") or g.get("words") or [])
                   if x in valid and x not in seen]
            if not ids:
                continue
            seen |= set(ids)
            groups.append(WordGroup(title=str(g.get("title") or "其他"), words=ids))
    except (LLMError, KeyError, TypeError):
        pass

    missing = [i for i, _, _ in items if i not in seen]
    if missing:
        # 保持素材顺序切块（句子即剧情顺序）
        for k in range(0, len(missing), GROUP_TARGET):
            chunk = missing[k : k + GROUP_TARGET]
            title = label if k == 0 else f"{label} {k // GROUP_TARGET + 1}"
            groups.append(WordGroup(title=title, words=chunk))
    return groups


def groups_to_dict(groups: list[WordGroup]) -> list[dict[str, Any]]:
    return [{"title": g.title, "words": g.words} for g in groups]


def groups_from_dict(data: list[dict[str, Any]]) -> list[WordGroup]:
    return [WordGroup(title=d["title"], words=list(d["words"])) for d in data]
