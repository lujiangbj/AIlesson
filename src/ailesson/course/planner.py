"""三层打包器：按教学点（词 + 短语 + 句子）计容量。

替代 packer.py 的纯词计量。原因见 assessment.py 的说明：CET-6 用户勾掉 48/53 个
词后只出 1 节课，但他不会的 26 个短语和 16 个句子被完全忽略了。

教学点 = 1 个词 / 1 个短语 / 1 个句子，一节课 6~10 个点。
短语和句子比词重，所以一节课的点数上限比纯词模式低。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ailesson.course.assessment import SelfAssessment
from ailesson.contract.episode import Episode
from ailesson.contract.lesson_spec import CoursePlan, LessonSpec
from ailesson.infra.llm import BaseLLM, LLMError

logger = logging.getLogger(__name__)

MIN_POINTS = 5          # 一节课教学点下限
MAX_POINTS = 10         # 上限
TARGET_POINTS = 8       # 目标
MAX_RETRY = 2
# 输出上限。151 个教学点时 8192 会被截断，实测需要更大
MAX_TOKENS = 16384
# 超过这么多教学点就分批打包。
# 定 120 是有依据的：peppa 全不会是 97 点，实测一次调用能正常出结果，
# 不该被卷进分批；Friends 的 157 点会让模型在 <thinking> 里耗尽 16k
# 预算（产出 44k 字符推演、零 JSON），必须切开。
BATCH_POINTS = 120


def validate_plan(
    ep: Episode, lessons: list[dict[str, Any]], a: SelfAssessment
) -> list[str]:
    """校验划分。错误信息会喂回 LLM 做重试反馈，写具体些。"""
    errs: list[str] = []
    seen: dict[str, set[str]] = {"words": set(), "chunks": set(), "sentences": set()}
    pools = {
        "words": set(a.unknown_words),
        "chunks": set(a.unknown_chunks),
        "sentences": set(a.unknown_sentences),
    }
    exists = {
        "words": {w.lemma for w in ep.words},
        "chunks": {c.id for c in ep.chunks},
        "sentences": {s.id for s in ep.sentences},
    }
    label = {"words": "词", "chunks": "短语", "sentences": "句子"}
    total_pool = sum(len(v) for v in pools.values())
    relax = total_pool < MIN_POINTS      # 池子本身装不满一节

    for i, l in enumerate(lessons, 1):
        got = {
            "words": list(l.get("words", []) or l.get("focus_words", [])),
            "chunks": list(l.get("chunks", []) or l.get("chunk_ids", [])),
            "sentences": list(l.get("sentences", []) or l.get("sentence_ids", [])),
        }
        n = sum(len(v) for v in got.values())
        lo = 1 if relax else MIN_POINTS
        if not (lo <= n <= MAX_POINTS):
            errs.append(
                f"第{i}节有 {n} 个教学点（词{len(got['words'])}+"
                f"短语{len(got['chunks'])}+句子{len(got['sentences'])}），"
                f"应为 {MIN_POINTS}~{MAX_POINTS} 个"
            )

        for dom, ids in got.items():
            for x in ids:
                if x not in exists[dom]:
                    errs.append(f"第{i}节引用了不存在的{label[dom]} {x}")
                    continue
                if x not in pools[dom]:
                    errs.append(
                        f"第{i}节的{label[dom]} {x} 不在待学池里（用户已勾会）"
                    )
                if x in seen[dom]:
                    errs.append(f"{label[dom]} {x} 在多节里重复出现")
                seen[dom].add(x)

    return errs


_SYSTEM = f"""你是英语课程的教学设计师。要把一集影视里「学习者还不会」的内容分成若干节课。

**教学点**有三种：单词、短语、句子。一节课 {MIN_POINTS}~{MAX_POINTS} 个教学点
（目标 {TARGET_POINTS} 个），约 30 分钟。三种点混着排，比例由待学内容决定 ——
如果学习者词汇量够、只是不会用，那这门课就该以短语和句子为主。

划分要求：
1. 每节 {MIN_POINTS}~{MAX_POINTS} 个教学点，只能从「待学清单」里选，不得重复
2. **同一节要围绕一个场景 / 一件事**，让这节课有主题。优先把相关的词、短语、句子
   放在一起 —— 学了 muddy 和 puddle，同一节就该练 "muddy puddles" 和
   "Peppa loves jumping in muddy puddles."
3. 尽量按剧情顺序推进（句子 id s01 → s16 是剧情顺序）
4. 给每节起中文主题名，5~12 字，说人话（例："在泥水洼里跳要穿雨靴"）
5. 已勾会的内容**不要**出现在任何一节里

只输出 JSON，不要解释：
{{"lessons": [{{"theme": "...", "words": [...], "chunks": [...], "sentences": [...]}}, ...]}}"""


def _build_prompt(ep: Episode, a: SelfAssessment, feedback: str = "") -> str:
    lines = [
        f"作品：{ep.title}（L{ep.level}，{ep.duration_seconds}秒）",
        "",
        f"【待学清单】共 {a.total_unknown()} 个教学点，全部都要安排进课程。",
    ]

    if a.unknown_words:
        lines += ["", f"◆ 待学单词 {len(a.unknown_words)} 个："]
        for w in a.unknown_words:
            word = ep.word(w)
            lines.append(f"  {w}（{word.meaning_zh}，出现{word.freq}次）")

    if a.unknown_chunks:
        lines += ["", f"◆ 待学短语 {len(a.unknown_chunks)} 个："]
        for cid in a.unknown_chunks:
            c = ep.chunk(cid)
            rel = f"，含词: {' '.join(c.covers_words)}" if c.covers_words else ""
            lines.append(f"  {cid}  \"{c.text}\"（{c.meaning_zh}{rel}）")

    if a.unknown_sentences:
        lines += ["", f"◆ 待学句子 {len(a.unknown_sentences)} 个（按剧情顺序）："]
        for sid in a.unknown_sentences:
            s = ep.sentence(sid)
            rel = f"，含短语: {' '.join(s.chunk_ids)}" if s.chunk_ids else ""
            lines.append(f"  {sid}  \"{s.text}\"（{s.meaning_zh}{rel}）")

    known_bits = []
    if a.known_words:
        known_bits.append(f"{len(a.known_words)} 个词")
    if a.known_chunks:
        known_bits.append(f"短语 {' '.join(a.known_chunks)}")
    if a.known_sentences:
        known_bits.append(f"句子 {' '.join(a.known_sentences)}")
    if known_bits:
        lines += ["", f"【已会（不要安排）】{' / '.join(known_bits)}"]

    expect = max(1, round(a.total_unknown() / TARGET_POINTS))
    lines += ["", f"预期划分成 {expect} 节左右。"]
    if feedback:
        lines += ["", f"上一次划分不合格，请修正后重新划分：\n{feedback}"]
    return "\n".join(lines)


def _rule_based(ep: Episode, a: SelfAssessment) -> list[dict[str, Any]]:
    """规则兜底：按剧情顺序扫句子，把它牵连的词和短语聚一起。

    只在 LLM 完全不可用时使用。分组尚可但主题名机械。
    """
    todo_w, todo_c = set(a.unknown_words), set(a.unknown_chunks)
    lessons: list[dict[str, Any]] = []
    cur: dict[str, list[str]] = {"words": [], "chunks": [], "sentences": []}

    def flush() -> None:
        if sum(len(v) for v in cur.values()):
            lessons.append({"theme": f"第{len(lessons) + 1}组", **{k: list(v) for k, v in cur.items()}})
            for k in cur:
                cur[k] = []

    for sid in a.unknown_sentences:
        s = ep.sentence(sid)
        add_c = [c for c in s.chunk_ids if c in todo_c]
        add_w = [w for w in ep.words_covered_by_sentence(sid) if w in todo_w]
        n_now = sum(len(v) for v in cur.values())
        if n_now and n_now + 1 + len(add_c) + len(add_w) > MAX_POINTS:
            flush()
        cur["sentences"].append(sid)
        for c in add_c:
            cur["chunks"].append(c); todo_c.discard(c)
        for w in add_w:
            cur["words"].append(w); todo_w.discard(w)
        if sum(len(v) for v in cur.values()) >= TARGET_POINTS:
            flush()
    flush()

    # 剩下的词和短语（没有句子牵连的）按块补课
    leftover = [("chunks", c) for c in a.unknown_chunks if c in todo_c]
    leftover += [("words", w) for w in a.unknown_words if w in todo_w]
    for i in range(0, len(leftover), TARGET_POINTS):
        block = leftover[i : i + TARGET_POINTS]
        item: dict[str, Any] = {"theme": f"补充 {i // TARGET_POINTS + 1}",
                                "words": [], "chunks": [], "sentences": []}
        for dom, x in block:
            item[dom].append(x)
        lessons.append(item)
    return lessons


def _distribute_bonus(
    ep: Episode, lessons: list[LessonSpec], left: dict[str, list[str]]
) -> None:
    """漏掉的条目摊进各节当顺带点。"""
    if not lessons:
        return
    n = len(lessons)
    for i, w in enumerate(left.get("words", [])):
        lessons[i % n].bonus_words.append(w)
    for i, c in enumerate(left.get("chunks", [])):
        lessons[i % n].bonus_chunks.append(c)
    for i, s in enumerate(left.get("sentences", [])):
        lessons[i % n].bonus_sentences.append(s)


def _split_assessment(a: SelfAssessment, size: int) -> list[SelfAssessment]:
    """把大待学池切成若干份，每份单独打包。

    为什么必须分批：157 个教学点一次喂进去，模型光在 <thinking> 里罗列
    就耗尽 16k token 预算，产出 44k 字符全是推演、一个 JSON 都没有。
    切成 80 点一批后每批都能正常出结果。

    切法：词按顺序均分，短语和句子按比例分摊到各批——每批都得有语境素材，
    否则某批会退化成纯词表（就是"补充 N"那种烂课）。
    """
    total = a.total_unknown()
    n = max(1, -(-total // size))
    if n == 1:
        return [a]

    def chop(items: list[str], k: int) -> list[list[str]]:
        per = -(-len(items) // k) if items else 0
        return [items[i * per:(i + 1) * per] for i in range(k)] if per else \
            [[] for _ in range(k)]

    ws = chop(list(a.unknown_words), n)
    cs = chop(list(a.unknown_chunks), n)
    ss = chop(list(a.unknown_sentences), n)

    out: list[SelfAssessment] = []
    for i in range(n):
        if not (ws[i] or cs[i] or ss[i]):
            continue
        out.append(SelfAssessment(
            episode_id=a.episode_id,
            known=dict(a.known),
            unknown={"words": ws[i], "chunks": cs[i], "sentences": ss[i]},
            how=a.how, at=a.at,
        ))
    return out or [a]


def pack_course(
    ep: Episode, a: SelfAssessment, llm: BaseLLM, thinking: bool = True
) -> CoursePlan:
    """按教学点打包成 N 节课。

    待学池超过 BATCH_POINTS 时分批调 LLM，各批结果拼起来并重排 index。
    """
    if a.total_unknown() == 0:
        return CoursePlan(episode_id=ep.id, lessons=[], at=int(time.time()))

    if a.total_unknown() > BATCH_POINTS:
        batches = _split_assessment(a, BATCH_POINTS)
        if len(batches) > 1:
            logger.info("待学 %d 点，分 %d 批打包", a.total_unknown(), len(batches))
            merged: list[LessonSpec] = []
            any_fb = False
            for part in batches:
                sub = pack_course(ep, part, llm, thinking=thinking)
                any_fb = any_fb or sub.fallback
                merged.extend(sub.lessons)
            for i, l in enumerate(merged, 1):
                l.index = i
            return CoursePlan(episode_id=ep.id, lessons=merged,
                               at=int(time.time()), fallback=any_fb)

    raw: list[dict[str, Any]] | None = None
    fallback = False
    fallback_reason = ""
    feedback = ""

    for attempt in range(MAX_RETRY + 1):
        try:
            data = llm.complete_json(
                _build_prompt(ep, a, feedback), system=_SYSTEM,
                thinking=thinking, max_tokens=MAX_TOKENS,
            )
            got = data["lessons"] if isinstance(data, dict) else data
        except (LLMError, KeyError, TypeError) as e:
            # 早先这里静默 break，打包失败对调用方完全不可见——
            # 用户拿到一份"第1组/补充N"的机械课表却不知道为什么。
            # 实测 151 个教学点会让 LLM 超时，必须留痕
            fallback_reason = f"{type(e).__name__}: {e}"[:160]
            logger.warning("打包 LLM 调用失败（第 %d 次）：%s",
                           attempt + 1, fallback_reason)
            break
        if not isinstance(got, list) or not got:
            feedback = "输出格式不对，lessons 必须是非空数组"
            fallback_reason = "LLM 输出不是非空数组"
            continue
        errs = validate_plan(ep, got, a)
        if not errs:
            raw = got
            break
        raw = got
        feedback = "\n".join(f"- {e}" for e in errs[:8])
        fallback_reason = f"校验未通过：{errs[0]}"[:160]

    if raw is None:
        logger.warning("退回机械划分（%d 个教学点）：%s",
                       a.total_unknown(), fallback_reason or "未知原因")
        raw, fallback = _rule_based(ep, a), True

    pools = {
        "words": set(a.unknown_words),
        "chunks": set(a.unknown_chunks),
        "sentences": set(a.unknown_sentences),
    }
    seen: dict[str, set[str]] = {k: set() for k in pools}
    lessons: list[LessonSpec] = []

    for l in raw:
        picked: dict[str, list[str]] = {}
        for dom, keys in (("words", ("words", "focus_words")),
                          ("chunks", ("chunks", "chunk_ids")),
                          ("sentences", ("sentences", "sentence_ids"))):
            ids: list[str] = []
            for k in keys:
                ids = list(l.get(k) or [])
                if ids:
                    break
            picked[dom] = [x for x in ids if x in pools[dom] and x not in seen[dom]]
            seen[dom] |= set(picked[dom])
        if not sum(len(v) for v in picked.values()):
            continue
        lessons.append(
            LessonSpec(
                episode_id=ep.id,
                index=len(lessons) + 1,
                theme=str(l.get("theme") or f"第{len(lessons) + 1}组"),
                focus_words=picked["words"],
                chunk_ids=picked["chunks"],
                sentence_ids=picked["sentences"],
            )
        )

    _distribute_bonus(ep, lessons, {
        dom: [x for x in pools[dom] if x not in seen[dom]] for dom in pools
    })
    return CoursePlan(
        episode_id=ep.id, lessons=lessons, at=int(time.time()), fallback=fallback
    )
