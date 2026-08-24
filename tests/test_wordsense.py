"""词义绑定与词形归并测试。

背景：judge_words 早先只收到裸词表，LLM 挑了最好画的同形异义词——
实测 7 个词义画错（split→香蕉船而非"平分"、pot→汤锅而非咖啡壶）。
"""
import pytest

from ailesson.content.wordsense import (
    base_lemma,
    clip_around,
    collect_senses,
    unspoken_words,
)


def line(text: str, direction: str | None = None) -> dict:
    return {"type": "line", "speaker": "X", "direction": direction, "text": text}


def scene(text: str) -> dict:
    return {"type": "scene", "text": text}


def stage(text: str) -> dict:
    return {"type": "stage", "text": text}


class TestBaseLemma:
    def test_动词变体归一(self):
        assert base_lemma("grabbed") == base_lemma("grab") == "grab"

    def test_复数归一(self):
        assert base_lemma("boots") == "boot"

    def test_缩略不归并(self):
        """don't 并到 do 会丢掉缩略形式本身的教学价值。"""
        assert base_lemma("don't") == "don't"

    def test_多原形时结果稳定(self):
        """一词多原形必须每次归到同一组，否则分组会漂。"""
        assert base_lemma("better") == base_lemma("better")

    def test_本身是原形则不归并(self):
        """left 有 VERB 原形 leave，但并过去会把"左边"和"离开"混成一张卡。"""
        assert base_lemma("left") == "left"
        assert base_lemma("married") == "married"

    def test_非原形才归并(self):
        assert base_lemma("was") == "be"
        assert base_lemma("stairs") == "stair"

    def test_未知词保持原样(self):
        assert base_lemma("brackety") == "brackety"


class TestCollectSenses:
    def test_收集原句(self):
        items = [line("Grab a spoon and eat."), line("She grabbed my arm.")]
        s = collect_senses(items, {"grab", "grabbed"})[0]
        assert s.lemma == "grab"
        assert len(s.examples) == 2

    def test_变体归并到一个_lemma(self):
        """grab/grabbed 只出一张卡——时态画不出来，两张互为干扰项无法作答。"""
        items = [line("Grab it."), line("He grabbed it."), line("Grab again.")]
        senses = collect_senses(items, {"grab", "grabbed"})
        assert len(senses) == 1
        assert senses[0].forms == {"grab": 2, "grabbed": 1}
        assert senses[0].count == 3

    def test_display_用最高频变体(self):
        items = [line("He grabbed it."), line("She grabbed me."), line("Grab it.")]
        assert collect_senses(items, {"grab", "grabbed"})[0].display == "grabbed"

    def test_剥掉内嵌舞台提示(self):
        """(She collapses) 里的词剧中听不到，不该被当例句。"""
        items = [line("Hi there. (She collapses onto the sofa.)")]
        senses = collect_senses(items, {"collapses", "hi"})
        assert [s.lemma for s in senses] == ["hi"]

    def test_例句不含括号内容(self):
        items = [line("I said (whispering) hello.")]
        s = collect_senses(items, {"hello"})[0]
        assert "whispering" not in s.examples[0]

    def test_忽略非台词项(self):
        items = [scene("A pot of coffee on the table"), stage("She exits."),
                 line("Want some?")]
        assert [s.lemma for s in collect_senses(items, {"pot", "exit", "want"})] \
            == ["want"]

    def test_例句数量上限(self):
        items = [line(f"Take {i} spoon.") for i in range(5)]
        assert len(collect_senses(items, {"spoon"}, max_examples=2)[0].examples) == 2

    def test_同句同_lemma_只取一次例句(self):
        items = [line("Grab it and grab again.")]
        s = collect_senses(items, {"grab"})[0]
        assert len(s.examples) == 1
        assert s.forms["grab"] == 2

    def test_按频次降序(self):
        items = [line("a a a b")]
        senses = collect_senses(items, {"a", "b"})
        assert [s.lemma for s in senses] == ["a", "b"]

    def test_长句裁剪(self):
        items = [line("word " * 60)]
        ex = collect_senses(items, {"word"})[0].examples[0]
        assert len(ex) <= 150      # 140 + 两端省略号


class TestClipAround:
    """长句裁剪必须保留目标词周围的上下文。

    硬切的后果：witness 那句被切在 "the barn raising scene i"，
    "n Witness"（电影名）丢了，词被画成法庭证人。
    """

    def test_短句原样返回(self):
        assert clip_around("Grab a spoon.", "spoon") == "Grab a spoon."

    def test_保留目标词(self):
        s = "a " * 100 + "witness " + "b " * 100
        out = clip_around(s, "witness")
        assert "witness" in out

    def test_不切断单词(self):
        s = "alpha bravo charlie delta echo foxtrot golf hotel " * 6
        out = clip_around(s, "delta", maxlen=60)
        for tok in out.strip(". ").split():
            assert tok in s.split(), f"切出了残词: {tok}"

    def test_中间截取两端加省略号(self):
        s = "x " * 80 + "target " + "y " * 80
        out = clip_around(s, "target")
        assert out.startswith("...") and out.endswith("...")

    def test_开头命中不加前省略号(self):
        s = "target " + "y " * 100
        assert not clip_around(s, "target").startswith("...")

    def test_长度受限(self):
        s = "word " * 200
        assert len(clip_around(s, "word", maxlen=80)) <= 90

    def test_目标词不在句中时退化为居中截取(self):
        s = "word " * 100
        out = clip_around(s, "missing", maxlen=60)
        assert len(out) <= 70 and out

    def test_序列化(self):
        items = [line("Grab a spoon.")]
        d = collect_senses(items, {"grab"})[0].to_dict()
        assert d["lemma"] == "grab" and d["count"] == 1
        assert "examples" in d


class TestUnspokenWords:
    def test_只在舞台提示出现的词(self):
        """实测 S1E1 有 41 个这种词（pot / cheer / collapses）。"""
        items = [line("Hello there. (She collapses.)"), stage("Time Lapse")]
        out = unspoken_words(items, {"collapses", "lapse", "hello"})
        assert out == {"collapses", "lapse"}

    def test_两处都出现的词不算(self):
        items = [line("She collapses now. (He collapses too.)")]
        assert unspoken_words(items, {"collapses"}) == set()

    def test_direction_字段也算舞台提示(self):
        items = [{"type": "line", "speaker": "X",
                  "direction": "sobbing", "text": "Hello."}]
        assert unspoken_words(items, {"sobbing"}) == {"sobbing"}

    def test_场景描述里的词算舞台提示(self):
        items = [scene("Central Perk, Monica is there"), line("Hi.")]
        assert "perk" in unspoken_words(items, {"perk", "hi"})

    def test_只返回问询范围内的词(self):
        items = [scene("A pot of coffee")]
        assert unspoken_words(items, {"pot"}) == {"pot"}
        assert unspoken_words(items, {"other"}) == set()
