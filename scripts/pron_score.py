#!/usr/bin/env python3
"""
本地免费发音评测（音素级 GOP 风格）
用法: /tmp/asr_demo_venv/bin/python pron_score.py <音频> "<参考文本>"
原理: wav2vec2 音素模型识别实际发音 vs espeak 参考发音 → 音素序列比对评分
"""
import sys, re, difflib
import torch, soundfile as sf
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor, Wav2Vec2FeatureExtractor, Wav2Vec2CTCTokenizer
from phonemizer.backend import EspeakBackend

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"
STRESS_RE = re.compile(r"[ˈˌ]")  # 去掉重音标记，只比对音素

def load_models():
    model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID)
    fe = Wav2Vec2FeatureExtractor.from_pretrained(MODEL_ID)
    tok = Wav2Vec2CTCTokenizer.from_pretrained(MODEL_ID)
    proc = Wav2Vec2Processor(feature_extractor=fe, tokenizer=tok)
    return model, proc

def actual_phonemes(model, proc, audio):
    """音频 → 实际音素序列（IPA）"""
    speech, sr = sf.read(audio)
    inputs = proc(speech, sampling_rate=sr, return_tensors="pt", padding=True)
    with torch.no_grad():
        logits = model(**inputs).logits
    ids = torch.argmax(logits, dim=-1)
    return proc.batch_decode(ids)[0]

def ref_phonemes(text):
    """参考文本 → 应发音素序列（IPA, espeak）"""
    backend = EspeakBackend("en-us", preserve_punctuation=False)
    phn = backend.phonemize([text])[0]
    return STRESS_RE.sub("", phn).replace(" ", "")

def normalize(seq):
    return STRESS_RE.sub("", seq).replace(" ", "").replace("\u200b", "")

def compare(actual, ref):
    """音素序列比对：相似度 + 差异点"""
    a, r = normalize(actual), normalize(ref)
    sm = difflib.SequenceMatcher(None, a, r)
    score = round(sm.ratio() * 100, 1)
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            diffs.append(f"实际'{a[i1:i2]}' 应为'{r[j1:j2]}'")
    return score, diffs

if __name__ == "__main__":
    audio, ref_text = sys.argv[1], sys.argv[2]
    model, proc = load_models()
    actual = actual_phonemes(model, proc, audio)
    ref = ref_phonemes(ref_text)
    print(f"实际发音: {actual}")
    print(f"参考发音: {ref}")
    score, diffs = compare(actual, ref)
    print(f"========== 发音评分: {score}/100 ==========")
    for d in diffs[:10]:
        print(f"  ⚠ {d}")
    if not diffs:
        print("  发音完全标准 ✓")
