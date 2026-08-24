"""素材加载层测试（NFR-4：数据层零改动，直接读 MVP 的 JSON）。"""
import pytest

from ailesson.contract.episode import Chunk, Episode, Sentence, Word, load_episode


@pytest.fixture(scope="module")
def e01(mvp_root) -> Episode:
    return load_episode(mvp_root, "peppa-s01e01")


class TestLoad:
    def test_基本信息(self, e01: Episode):
        assert e01.id == "peppa-s01e01"
        assert e01.title == "Muddy Puddles"
        assert e01.level == 1

    def test_三层素材数量(self, e01: Episode):
        assert len(e01.words) == 53
        assert len(e01.chunks) == 28
        assert len(e01.sentences) == 16

    def test_词字段完整(self, e01: Episode):
        w = e01.word("puddle")
        assert isinstance(w, Word)
        assert w.freq == 28
        assert w.meaning_zh == "水洼"
        assert w.audio.endswith("puddle.mp3")
        assert w.audio_slow.endswith("puddle_slow.mp3")
        assert w.image.endswith(".webp")

    def test_chunk_覆盖词(self, e01: Episode):
        c = e01.chunk("jump_in_puddles")
        assert isinstance(c, Chunk)
        assert c.text == "jump in muddy puddles"
        assert set(c.covers_words) == {"jump", "muddy", "puddle"}

    def test_句子含原片切片与归属(self, e01: Episode):
        s = e01.sentence("s08")
        assert isinstance(s, Sentence)
        assert s.text == "If you jump in muddy puddles, you must wear your boots."
        assert s.audio_clip.endswith("s08.mp3")
        assert "wear_boots" in s.chunk_ids
        assert "boot" in s.key_words

    def test_干扰项(self, e01: Episode):
        d = e01.distractors_for("puddle")
        assert len(d) == 3
        assert "puddle" not in d


class TestCoverage:
    """FR-3.2 打包依据：句子 → 词覆盖关系。"""

    def test_句子覆盖词_含chunk带的词(self, e01: Episode):
        # s08 的 key_words 有 boot/wear，chunk wear_boots 也带这两个
        covered = e01.words_covered_by_sentence("s08")
        assert {"jump", "muddy", "puddle", "wear", "boot"} <= covered

    def test_覆盖的词都在词表里(self, e01: Episode):
        lemmas = {w.lemma for w in e01.words}
        for s in e01.sentences:
            assert e01.words_covered_by_sentence(s.id) <= lemmas

    def test_长尾词_未被任何句子覆盖(self, e01: Episode):
        """E01 实测有 15 个长尾词，FR-3.4 要求它们作为顺带词处理。"""
        tail = e01.tail_words()
        assert len(tail) == 15
        assert "bath" in tail
        assert "garden" in tail
        # 高频核心词一定被句子覆盖
        assert "puddle" not in tail
        assert "jump" not in tail

    def test_被覆盖词数(self, e01: Episode):
        assert len(e01.covered_words()) == 38
        assert len(e01.covered_words()) + len(e01.tail_words()) == len(e01.words)


class TestAssetPaths:
    def test_音频图片文件真实存在(self, e01: Episode, mvp_root):
        for w in e01.words[:10]:
            assert (mvp_root / w.audio).exists(), w.audio
            if not w.skip_image:
                assert (mvp_root / w.image).exists(), w.image

    def test_句子原片切片存在(self, e01: Episode, mvp_root):
        for s in e01.sentences:
            assert (mvp_root / s.audio_clip).exists(), s.audio_clip
