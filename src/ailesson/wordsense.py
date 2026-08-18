"""给每个待配图的词绑定剧中原句，并归并词形变体。

为什么需要：judge_words() 早先只收到一个裸词表，没有上下文，LLM 只能挑
最好画的那个同形异义词——实测 7 个词义画错（split→香蕉船而非"分开"、
hump→驼峰而非驼背、figure→人体素描而非"猜想"、pot→汤锅而非咖啡壶）。

同时把 grab/grabbed 这类变体收敛到一个 lemma：时态画不出来，两张卡
互为干扰项时无法作答。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lemminflect import getAllLemmas

from .vocab_cefr import WORD_RE, spoken_only

# 一个词最多带几句例句进 prompt。多了吃 token，少了词义定不准
MAX_EXAMPLES = 2
# 例句长度上限。硬切会毁掉词义线索：witness 那句被切成
# "...the barn raising scene i"，正好断在 "n Witness" 前，LLM 没看到
# Witness 是电影名，把词画成了法庭证人。所以改成按词边界裁剪，
# 且优先保留目标词周围的窗口
EXAMPLE_MAXLEN = 140


@dataclass
class WordSense:
    """一个 lemma 及其在剧中的实际用法。"""
    lemma: str
    forms: dict[str, int] = field(default_factory=dict)   # 变体 → 出现次数
    examples: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(self.forms.values())

    @property
    def display(self) -> str:
        """卡片上显示的词形：用出现最多的那个变体。"""
        return max(self.forms.items(), key=lambda kv: kv[1])[0] if self.forms else self.lemma

    def to_dict(self) -> dict:
        return {
            "lemma": self.lemma,
            "display": self.display,
            "count": self.count,
            "forms": self.forms,
            "examples": self.examples,
        }


# 一词多原形时按此优先级取，保证同一个词每次归到同一组
POS_ORDER = ("VERB", "NOUN", "ADJ", "ADV", "AUX")


def base_lemma(word: str) -> str:
    """取一个词的规范原形，用于归并变体（grabbed→grab、boots→boot）。

    两条保守规则，避免过度归并：
    1. 缩略形式不并——don't 并到 do 会丢掉缩略本身的教学价值
    2. **词本身已是某个词性的原形时保持原样**——left 有 ADJ 原形 left 和
       VERB 原形 leave，并到 leave 会把"左边"和"离开"混成一张卡；
       married 同理（ADJ 原形是自身，剧里就是形容词用法）
    """
    if "'" in word:
        return word
    lemmas = getAllLemmas(word)
    if not lemmas:
        return word
    if any(word == f.lower() for g in lemmas.values() for f in g):
        return word
    for pos in POS_ORDER:
        if pos in lemmas and lemmas[pos]:
            return lemmas[pos][0].lower()
    return word


def clip_around(sentence: str, word: str, maxlen: int = EXAMPLE_MAXLEN) -> str:
    """把长句裁到 maxlen 以内，保留目标词周围的上下文，且不切断单词。

    直接 sentence[:maxlen] 会毁掉词义线索——witness 那句被切在
    "the barn raising scene i" 处，"n Witness"（电影名）丢了，
    结果词被画成法庭证人。这里以目标词为中心取窗口，两端按空格对齐。
    """
    if len(sentence) <= maxlen:
        return sentence

    m = re.search(rf"\b{re.escape(word)}\b", sentence, re.I)
    center = (m.start() + m.end()) // 2 if m else len(sentence) // 2
    half = maxlen // 2
    start, end = max(0, center - half), min(len(sentence), center + half)
    # 长度不够时往另一侧补
    if end - start < maxlen:
        if start == 0:
            end = min(len(sentence), maxlen)
        else:
            start = max(0, end - maxlen)

    chunk = sentence[start:end]
    # 两端对齐到词边界，避免半个单词
    if start > 0 and " " in chunk:
        chunk = chunk[chunk.index(" ") + 1:]
    if end < len(sentence) and " " in chunk:
        chunk = chunk[:chunk.rindex(" ")]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(sentence) else ""
    return f"{prefix}{chunk.strip()}{suffix}"


def collect_senses(
    items: list[dict],
    words: set[str],
    *,
    max_examples: int = MAX_EXAMPLES,
) -> list[WordSense]:
    """从台词里给指定的词收集原句，并按 lemma 归并。

    只看 type=="line" 且剥掉内嵌舞台提示后的文本——提示里的词听不到。
    """
    senses: dict[str, WordSense] = {}

    for it in items:
        if it.get("type") != "line":
            continue
        spoken = spoken_only(it["text"]).strip()
        if not spoken:
            continue
        sentence = re.sub(r"\s+", " ", spoken)

        seen_here: set[str] = set()
        for raw in WORD_RE.findall(sentence):
            w = raw.lower()
            if w not in words:
                continue
            key = base_lemma(w)
            s = senses.setdefault(key, WordSense(lemma=key))
            s.forms[w] = s.forms.get(w, 0) + 1
            # 同一句里同一个 lemma 只取一次例句
            if key not in seen_here and len(s.examples) < max_examples:
                s.examples.append(clip_around(sentence, w))
                seen_here.add(key)

    return sorted(senses.values(), key=lambda s: -s.count)


def unspoken_words(items: list[dict], words: set[str]) -> set[str]:
    """找出只在舞台提示里出现、从未被说出口的词。

    这些词学习者在剧里听不到，不该进教学池。
    """
    spoken: set[str] = set()
    staged: set[str] = set()

    for it in items:
        text = it.get("text", "")
        if it.get("type") == "line":
            said = spoken_only(text)
            spoken.update(w.lower() for w in WORD_RE.findall(said))
            # 括号内的部分算舞台提示
            inline = " ".join(re.findall(r"\(([^)]*)\)", text))
            staged.update(w.lower() for w in WORD_RE.findall(inline))
            if it.get("direction"):
                staged.update(w.lower() for w in WORD_RE.findall(it["direction"]))
        else:
            staged.update(w.lower() for w in WORD_RE.findall(text))

    return (staged & words) - spoken
