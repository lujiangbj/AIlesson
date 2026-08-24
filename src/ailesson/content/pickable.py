"""判断一个词能不能用单张图教（配图可行性）。

为什么需要这一层：PRD FR-4.1 的 5 次曝光里，环节 3「听音选图」和 4「看图选音」
都依赖图片。配不出无歧义图的词走不完这 5 次，硬生图是浪费请求。

实测反例（gen_grid 首轮）：
- furniture 画成「扶手椅+边桌+茶几」一组，小孩只会说 chair —— 集合名词单图教不了
- freezer 画成白色闭合箱体，跟洗衣机/工具箱分不清 —— 需要冰霜等状态线索

所以除了「能不能画」，还要问「画出来会不会被认成另一个词」。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from ailesson.infra.llm import BaseLLM, LLMError

# 判定结果
GOOD = "good"          # 单图能教，无歧义
RISKY = "risky"        # 能画但易混，需要额外线索（prompt 里给状态/对比）
UNPICKABLE = "no"      # 抽象/功能词/集合名词，单图教不了

# subject 里要求印字/标号的迹象。带引号的字面量、label/sign 类词、
# 以及 1st~9th、"1,000" 这种序数和带千分位的数字。
# prompt 已明令禁止，但实测仍有 20/242 个 subject 泄漏，所以加代码兜底
TEXT_DEMAND_RE = re.compile(
    r"""["'][^"']{1,40}["']
      | \b(?:label|labelled|labeled|labelling|caption|captioned
          |text|lettering|letters|word|words|spelling|spelled
          |written|writing|writes|reading|reads|says
          |number|numeral|numerals|digit|digits|sign|signage
          |nameplate|banner|placard|scoreboard|bib)\b
      | \b\d+(?:st|nd|rd|th)\b
      | \b\d{1,3}(?:,\d{3})+\b
    """,
    re.I | re.X,
)


@dataclass
class PickVerdict:
    word: str
    verdict: str = UNPICKABLE
    subject: str | None = None    # 建议画什么（喂给生图 prompt）
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.verdict in (GOOD, RISKY)

    def to_dict(self) -> dict:
        d = {"word": self.word, "verdict": self.verdict}
        if self.subject:
            d["subject"] = self.subject
        if self.reason:
            d["reason"] = self.reason
        return d


SYSTEM = """你在为**成人**英语学习者筛选可配图的单词。课程有两个环节靠图片：
听音选图（听单词，从 4 张图里选）、看图选音（看图，从 4 个读音里选）。

判定门槛是「**成人在四选一里能否排除干扰项**」，不是「看图能否脱口说出这个词」。
四选一有对比、有语境，门槛比你以为的低得多。成人具备抽象推理能力，
能看懂对比构图、夸张表情、动作线、图形隐喻。

素材来自美剧台词。**不要因为"不适合儿童"而排除任何词**：酒精、身体部位、
成人话题照常判定，只按"能不能画清楚"这一个标准。

⚠️ 默认倾向是**能画**。判 "no" 前先穷尽三种手法：
· 对比构图——两个状态并置（apart 画两块分开的拼图；slowly 画蜗牛 vs 兔子）
· 夸张表情/肢体——情绪和状态形容词一律走这条（gorgeous 惊艳到瞪眼捂嘴；
  horrible 嫌恶皱脸捏鼻；intense 咬牙瞪眼额头暴汗）
· 典型场景——集合名词和抽象名词走这条（furniture 画摆满家具的客厅全景；
  silence 画图书馆里众人竖指噓声；trouble 画办公室里被上司指着骂）

对每个词判定：
- "good": 单张图能清楚表达。具体物体、明确动作、可辨识场景或表情。
  例：spoon / boots / run / angry / gorgeous / hit
- "risky": 能画但容易被认成别的词，需要额外视觉线索才行。
  例：freezer(白箱子像洗衣机 → 要画开盖+霜+冰) / cold(要画打哆嗦+雪)
- "no": **只留给真正没有视觉形态的词**，严格限于：
  · 连词、介词（although / however / unless / while / without / across）
  · 语气词、招呼语（yeah / hey / alright / huh）
  · 助动词、代词、限定词（myself / herself / such / whole / few / enough）
  · 纯语法性副词（actually / anyway / exactly / mostly / hardly / whatsoever）
  抽象名词和形容词**不要**直接判 no——先试上面三种手法。
  实在画不出再判 no，并且 reason 要说明你试过哪种手法为何不行。

写 subject（给 good 和 risky）：一句英文，描述该画什么，具体到能直接喂给
生图模型。risky 的词必须写明区分线索。四条约定：
1. 涉及人物时，明确说画完整的人（full figure / a woman doing X），
   不要只说"a person's hand"——除非该词本身就是手部动作。
2. **关系类词**（married / divorced / friend / couple）必须写成**两个完整
   人物同框的场景**，并给出能表达该关系的可见线索（服装、姿态、场景、
   道具）。不要写成手部或戒指特写——那会被认成 hand 或 ring。
3. **subject 里绝对不要出现任何英文文字、数字、标签、号码**。
   这是硬规则：卡片用于「听音选图」四选一，图上印了词或数字就等于把答案
   写在卡面上，题目作废。实测翻车例：last 被画成运动员胸前写 "4th/LAST"、
   fifth 被画成五个号码布标 1st~5th——都成了废卡。
   序数、数量、顺序一律用**可数的视觉元素**表达，不用数字：
   fifth → 五个同样的杯子排一行，第五个被一只手拿起、其余四个留在原位
   thousand → 密密麻麻铺满画面的同款硬币
   plus → 两堆苹果被一个大号手势合并成一堆
   若某个词离了文字**根本无法**表达，判 "no"，reason 写"须依赖文字"。
4. **不要用暗示性道具代替概念本体**。若一个词只能靠"挂在门把上的领带"
   这种旁敲侧击的道具来表达，图的主体就变成了那个道具（门/领带），
   学习者认不出目标词——这种情况判 "no"，不要硬凑。
   注意这条针对的是"道具替代"，不是"场景表达"：画满家具的客厅来表达
   furniture 是合法的场景表达，因为主体就是家具本身。
5. **背景必须是纯色，主体单一**。不要写建筑群、街景、山川、看台人群
   这类完整场景——那会盖掉主体，破坏图卡的辨识度。需要环境交代时，
   只画一两件必要道具。实测翻车例：finally 画了庙宇+山路+灌木，
   读起来像"毕业"而不是"终于"。
6. **必须画剧中的那个词义**。每个词会附上它在剧里的原句，
   subject 必须对应原句里的用法，不要挑更好画的另一个义项。
   实测翻车例：split 在剧里是"平分"，被画成香蕉船冰淇淋；
   figure 在剧里是"我猜"，被画成人体素描课；pot 在剧里是咖啡壶，
   被画成汤锅。若原句里的义项画不出来，判 "no"，不要改画另一个义项。
"no" 的词 subject 填 null，reason 写为什么不行（中文，12 字内）。

另外：涉及性行为、裸露、暴力血腥的词，生图模型会拒绝或产出无效图，
一律判 "no"，reason 写"生图受限"。这与成人学习者无关，是生成侧限制。

只输出 JSON 数组：
[{"word":"spoon","verdict":"good","subject":"a single silver teaspoon","reason":null},
 {"word":"freezer","verdict":"risky","subject":"an open chest freezer with frost and ice cubes inside, cold blue mist","reason":null},
 {"word":"although","verdict":"no","subject":null,"reason":"连词，无实体"}]"""


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _payload(group: list[str], senses: dict[str, list[str]] | None) -> str:
    """拼请求体。带例句时用对象形式，让 LLM 看到词在剧里的实际用法。"""
    if not senses:
        return json.dumps(group, ensure_ascii=False)
    rows = [{"word": w, "lines": senses.get(w, [])} for w in group]
    return json.dumps(rows, ensure_ascii=False, indent=1)


def _ask(
    group: list[str],
    llm: BaseLLM,
    context: str | None,
    max_tokens: int,
    senses: dict[str, list[str]] | None = None,
) -> list | None:
    """问一批，返回 JSON 数组；失败给 None 让调用方决定拆批重试。"""
    prompt = _payload(group, senses)
    if context:
        prompt = f"这些词来自：{context}\n\n{prompt}"
    if senses:
        prompt += ("\n\n每个词的 lines 是它在剧中的原句。"
                   "subject 必须对应原句里的义项。")
    try:
        data = llm.complete_json(prompt, system=SYSTEM, max_tokens=max_tokens)
    except LLMError:
        return None
    return data if isinstance(data, list) else None


def judge_words(
    words: list[str],
    llm: BaseLLM,
    *,
    batch: int = 10,
    context: str | None = None,
    max_tokens: int = 16384,
    senses: dict[str, list[str]] | None = None,
) -> list[PickVerdict]:
    """批量判定配图可行性。

    senses 给 word → 剧中原句列表。**强烈建议传**：不传的话 LLM 只能挑
    最好画的同形异义词，实测 7 个词义画错（split→香蕉船而非"平分"、
    figure→人体素描而非"我猜"、pot→汤锅而非咖啡壶）。

    batch 别调大：每个词要回一句完整场景描述的 subject，很吃 token。
    实测 batch=40/max_tokens=8192 有 3/8 批被截断（白丢 107 词），
    batch=20 仍有 2/16 批截断（白丢 20 词）。现在 batch=10 + 失败自动拆半重试。
    """
    out: list[PickVerdict] = []
    for group in _chunks(words, batch):
        data = _ask(group, llm, context, max_tokens, senses)
        # 整批解析失败多半是输出被 max_tokens 截断，对半拆再试一次
        if data is None and len(group) > 1:
            mid = len(group) // 2
            halves = [_ask(group[:mid], llm, context, max_tokens, senses),
                      _ask(group[mid:], llm, context, max_tokens, senses)]
            data = [row for h in halves if h for row in h] or None
        if data is None:
            out += [PickVerdict(w, reason="LLM 判定失败") for w in group]
            continue

        by_word = {}
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("word"):
                    by_word[str(row["word"]).strip().lower()] = row

        for w in group:
            row = by_word.get(w)
            if not row:
                out.append(PickVerdict(w, reason="LLM 未返回"))
                continue
            v = str(row.get("verdict") or UNPICKABLE).strip().lower()
            if v not in (GOOD, RISKY, UNPICKABLE):
                v = UNPICKABLE
            subject = (row.get("subject") or "").strip() or None
            # 判 good/risky 但没给 subject 的，降级——生图没素材
            if v in (GOOD, RISKY) and not subject:
                v = UNPICKABLE
            # 兜住"文字泄漏"：prompt 明令禁止仍有 20/242 个 subject 要求印字，
            # 图上印了词或数字就等于把答案写在卡面上，题目作废
            elif v in (GOOD, RISKY) and TEXT_DEMAND_RE.search(subject or ""):
                v, subject = UNPICKABLE, None
                row = {**row, "reason": "须依赖文字"}
            out.append(PickVerdict(
                word=w, verdict=v, subject=subject,
                reason=(row.get("reason") or "").strip() or None,
            ))
    return out
