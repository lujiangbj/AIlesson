"""听力探测测试。

核心设计（用户纠正）：不能用单词掌握度推断 chunk/句子难度——
习语和连读是独立难点。必须抽样实测，再校准推断。
"""
import pytest

from ailesson.probe import (
    Calibration,
    Item,
    build_items,
    calibrate,
    difficulty,
    features,
    infer_unknown,
    stratified_probe,
)


class TestFeatures:
    def test_数词和生词(self):
        f = features("Grab a spoon and eat", {"spoon"})
        assert f.n_words == 5
        assert f.n_unknown == 1
        assert f.unknown_ratio == pytest.approx(0.2)

    def test_识别习语(self):
        assert features("I'm gonna grab it", set()).n_idiom >= 1

    def test_识别缩略(self):
        assert features("I'm sure it's fine", set()).n_contraction == 2

    def test_识别弱读组合(self):
        assert features("a bunch of the stuff", set()).n_reduction >= 1

    def test_空文本(self):
        f = features("", {"x"})
        assert f.n_words == 0 and f.unknown_ratio == 0.0


class TestDifficulty:
    def test_生词越多越难(self):
        a = difficulty(features("The gravy and the hump", {"gravy", "hump"}))
        b = difficulty(features("The gravy and the hump", set()))
        assert a > b

    def test_习语独立增加难度(self):
        """全是熟词也可能听不懂——这是不能靠单词量推断的核心原因。"""
        easy = difficulty(features("I will sleep on the sofa", set()))
        hard = difficulty(features("You gonna crash on the couch", set()))
        assert hard > easy

    def test_连读增加难度(self):
        a = difficulty(features("I'm sure it's here", set()))
        b = difficulty(features("I am sure it is here", set()))
        assert a > b

    def test_长句更难(self):
        short = difficulty(features("Grab a spoon", set()))
        long = difficulty(features("Grab a spoon " + "and more " * 8, set()))
        assert long > short


class TestBuildItems:
    def test_区分_kind(self):
        items = build_items([("c1", "kind of")], [("s1", "It is kind of nice")],
                            set())
        assert {i.kind for i in items} == {"chunk", "sentence"}

    def test_带难度分(self):
        items = build_items([("c1", "gonna go")], [], set())
        assert items[0].diff > 0


class TestStratifiedProbe:
    @pytest.fixture
    def items(self):
        # 造难度递增的 20 个 chunk + 20 个句子
        cs = [(f"c{i}", "word " * (i + 1)) for i in range(20)]
        ss = [(f"s{i}", "I'm gonna " + "word " * (i + 1)) for i in range(20)]
        return build_items(cs, ss, set())

    def test_数量受限(self, items):
        assert len(stratified_probe(items, n=8)) <= 8

    def test_两类都抽到(self, items):
        got = stratified_probe(items, n=8)
        assert {i.kind for i in got} == {"chunk", "sentence"}

    def test_难易两端都覆盖(self, items):
        """均匀抽会挤在中等难度，测不出能力边界。"""
        got = stratified_probe(items, n=16)
        ds = sorted(i.diff for i in got)
        alld = sorted(i.diff for i in items)
        span = (ds[-1] - ds[0]) / (alld[-1] - alld[0])
        assert span > 0.5, "抽样应横跨难度区间"

    def test_不重复(self, items):
        got = stratified_probe(items, n=16)
        assert len({i.id for i in got}) == len(got)

    def test_空输入(self):
        assert stratified_probe([], n=8) == []

    def test_只有一类也能抽(self):
        items = build_items([("c1", "a"), ("c2", "b")], [], set())
        assert stratified_probe(items, n=4)


def It(iid, diff, kind="sentence"):
    return Item(id=iid, kind=kind, text="x", diff=diff)


class TestCalibrate:
    def test_找到切分点(self):
        """低难度听懂、高难度没听懂 → 阈值应落在中间。"""
        res = [(It("a", 1.0), True), (It("b", 2.0), True),
               (It("c", 5.0), False), (It("d", 6.0), False)]
        cal = calibrate(res)
        assert 2.0 < cal.threshold < 5.0
        assert cal.accuracy == 1.0

    def test_全听懂(self):
        res = [(It(str(i), float(i)), True) for i in range(6)]
        cal = calibrate(res)
        assert cal.n_understood == 6
        assert not cal.confident, "无区分度，阈值不可信"

    def test_全听不懂(self):
        res = [(It(str(i), float(i)), False) for i in range(6)]
        assert not calibrate(res).confident

    def test_样本太少不可信(self):
        res = [(It("a", 1.0), True), (It("b", 5.0), False)]
        assert not calibrate(res).confident

    def test_容忍噪声(self):
        """用户可能蒙对，不强求完美切分。"""
        res = [(It("a", 1.0), True), (It("b", 2.0), False),   # 噪声
               (It("c", 3.0), True), (It("d", 8.0), False),
               (It("e", 9.0), False), (It("f", 1.5), True)]
        cal = calibrate(res)
        assert cal.confident
        assert 0.5 < cal.accuracy < 1.0

    def test_空结果(self):
        cal = calibrate([])
        assert cal.n_probed == 0 and not cal.confident


class TestInferUnknown:
    @pytest.fixture
    def items(self):
        return [It("c1", 1.0, "chunk"), It("c2", 7.0, "chunk"),
                It("s1", 2.0), It("s2", 8.0)]

    def test_按阈值推断(self, items):
        cal = Calibration(threshold=4.0, accuracy=1.0, n_probed=8,
                          n_understood=4)
        out = infer_unknown(items, cal)
        assert out["chunks"] == ["c2"]
        assert out["sentences"] == ["s2"]

    def test_实测结果优先于推断(self, items):
        """探测过的条目用真实答案，不用阈值猜。"""
        cal = Calibration(threshold=4.0, accuracy=1.0, n_probed=8,
                          n_understood=4)
        out = infer_unknown(items, cal, probed={"c2": True, "s1": False})
        assert "c2" not in out["chunks"], "实测听懂了，不该判为待学"
        assert "s1" in out["sentences"], "实测没听懂，应判为待学"

    def test_阈值不可信时保守(self, items):
        """不能因为没校准好就把整集塞进待学池。"""
        cal = Calibration(threshold=0.0, accuracy=0.5, n_probed=2,
                          n_understood=2)
        out = infer_unknown(items, cal)
        n = len(out["chunks"]) + len(out["sentences"])
        assert n < len(items)

    def test_无探测数据时用默认阈值(self, items):
        out = infer_unknown(items, Calibration())
        assert out["chunks"] == ["c2"] and out["sentences"] == ["s2"]

    def test_空条目(self):
        out = infer_unknown([], Calibration(threshold=1.0))
        assert out == {"chunks": [], "sentences": []}
