"""剧本切段测试。

核心约束：切点只落在换场边界，绝不断在场景中途。
"""
import pytest

from ailesson.content.segment import (
    Chunk,
    segment_episode,
    split_chunks,
    spread,
)


def line(text: str) -> dict:
    return {"type": "line", "speaker": "X", "direction": None, "text": text}


def scene(text: str) -> dict:
    return {"type": "scene", "text": text}


def stage(text: str) -> dict:
    return {"type": "stage", "text": text}


def words(n: int) -> dict:
    """造一句 n 个词的台词。"""
    return line(" ".join(["word"] * n))


@pytest.fixture
def items() -> list[dict]:
    """4 个场景，词数 10 / 20 / 30 / 40。"""
    out = []
    for i, (loc, n) in enumerate(
        [("Central Perk", 10), ("The Subway", 20),
         ("Ross's Apartment", 30), ("Iridium", 40)]
    ):
        out.append(scene(f"{loc}, people are there."))
        out.append(words(n))
    return out


class TestSplitChunks:
    def test_按换场切(self, items):
        assert len(split_chunks(items)) == 4

    def test_舞台提示不算边界(self):
        """Time Lapse 是同场内时间跳跃，切在那里会拦腰截断对话。"""
        items = [scene("Central Perk"), words(5),
                 stage("Time Lapse"), words(5)]
        chunks = split_chunks(items)
        assert len(chunks) == 1
        assert chunks[0].words == 10

    def test_丢掉无台词的_chunk(self):
        items = [scene("空场"), scene("Central Perk"), words(5)]
        assert len(split_chunks(items)) == 1

    def test_开场未标注也保留(self):
        chunks = split_chunks([words(5), scene("Central Perk"), words(5)])
        assert chunks[0].scene is None
        assert chunks[0].location == "开场"

    def test_地点去掉走位描述(self, items):
        assert split_chunks(items)[0].location == "Central Perk"


class TestSegmentEpisode:
    def test_切点不断在场景中途(self, items):
        """每段的 chunk 必须是完整的，段内词数等于所含 chunk 词数之和。"""
        for seg in segment_episode(items, n=2):
            assert seg.words == sum(c.words for c in seg.chunks)
            for c in seg.chunks:
                assert c.lines, "chunk 不该为空"

    def test_不丢内容(self, items):
        segs = segment_episode(items, n=3)
        assert sum(s.words for s in segs) == 100
        assert sum(len(s.lines) for s in segs) == 4

    def test_顺序保持(self, items):
        segs = segment_episode(items, n=2)
        assert [s.index for s in segs] == [1, 2]
        first = segs[0].chunks[0].location
        assert first == "Central Perk", "剧情顺序不能乱"

    def test_DP_比贪心均匀(self):
        """贪心会把余量堆在末尾；DP 求全局最优。

        词数 [30,10,10,50] 切 2 段：贪心切 [30] / [10,10,50]（30 vs 70），
        DP 应切 [30,10,10] / [50]（50 vs 50）。
        """
        items = []
        for n in (30, 10, 10, 50):
            items += [scene(f"S{n}"), words(n)]
        segs = segment_episode(items, n=2)
        assert [s.words for s in segs] == [50, 50]

    def test_段数不超过_chunk_数(self, items):
        """4 个场景要不出 10 段——不拆分单个场景。"""
        assert len(segment_episode(items, n=10)) == 4

    def test_每段非空(self, items):
        for seg in segment_episode(items, n=4):
            assert seg.chunks

    def test_自动选份数(self, items):
        """不指定 n 时在 4~6 搜；4 个 chunk 上限是 4 段。"""
        segs = segment_episode(items)
        assert 1 <= len(segs) <= 4

    def test_自动选最均匀方案(self):
        """6 个等长 chunk：切 6 段完美均分，应选 6 而非 4。"""
        items = []
        for i in range(6):
            items += [scene(f"S{i}"), words(20)]
        segs = segment_episode(items, n_range=(4, 6))
        assert len(segs) == 6
        assert spread(segs) == pytest.approx(1.0)

    def test_空输入(self):
        assert segment_episode([]) == []

    def test_只有舞台提示无换场(self):
        """整集没 Scene: 标记时退化成 1 段，不该崩。"""
        segs = segment_episode([words(10), stage("Pause"), words(10)])
        assert len(segs) == 1
        assert segs[0].words == 20


class TestSegmentProps:
    def test_locations_去重保序(self):
        items = [scene("Central Perk, a"), words(5),
                 scene("Iridium, b"), words(5),
                 scene("Central Perk, c"), words(5)]
        seg = segment_episode(items, n=1)[0]
        assert seg.locations == ["Central Perk", "Iridium"]

    def test_texts_按顺序(self):
        items = [scene("S"), line("first"), line("second")]
        assert segment_episode(items, n=1)[0].texts == ["first", "second"]

    def test_序列化(self, items):
        d = segment_episode(items, n=2)[0].to_dict()
        assert d["index"] == 1
        assert "locations" in d and "words" in d


class TestSpread:
    def test_完美均分是_1(self):
        items = [scene("A"), words(10), scene("B"), words(10)]
        assert spread(segment_episode(items, n=2)) == pytest.approx(1.0)

    def test_空列表(self):
        assert spread([]) == 0.0
