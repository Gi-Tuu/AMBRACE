# -*- coding: utf-8 -*-
"""Phase 2 P2-2：服务端轻量 VAD 纯函数（audio_gate）。

覆盖：
- should_transcribe：空字节 False；静音帧 False；正常帧 True；
- 极短帧（<300ms）即使有声也 False；
- 无法解析的压缩/非 WAV（m4a 等）→ 保守回退 True（不阻塞 ASR）；
- 阈值边界（时长/静音 RMS）与异常 fail-open。
"""
import math
import struct

from app.voice import audio_gate


def _wav_bytes(samples, sample_rate=16000, channels=1, bits=16):
    """按 16-bit PCM 组装一个最小合法 WAV 字节串（用于测试）。"""
    if bits == 16:
        pcm = b"".join(struct.pack("<h", s) for s in samples)
    elif bits == 8:
        pcm = bytes((s + 128) & 0xFF for s in samples)
    else:
        raise ValueError("test helper supports 8/16-bit only")
    bps = bits // 8
    byte_rate = sample_rate * channels * bps
    block_align = channels * bps
    data_size = len(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", data_size)
    return header + pcm


def _sine(frames, sample_rate=16000, freq=440.0, amp=12000):
    out = []
    for i in range(frames):
        out.append(int(amp * math.sin(2 * math.pi * freq * i / sample_rate)))
    return out


def test_should_transcribe_empty_bytes_false():
    assert audio_gate.should_transcribe(b"") is False
    assert audio_gate.should_transcribe(None) is False  # type: ignore[arg-type]


def test_should_transcribe_silent_frame_false():
    # 1 秒纯静音（RMS=0）→ False
    wav = _wav_bytes([0] * 16000)
    assert audio_gate.should_transcribe(wav) is False


def test_should_transcribe_normal_frame_true():
    # 0.5 秒有声正弦波、非静音 → True
    wav = _wav_bytes(_sine(8000))
    assert audio_gate.should_transcribe(wav) is True


def test_should_transcribe_very_short_frame_false():
    # 0.1 秒（<300ms）即便有声也 False（省 ASR）
    wav = _wav_bytes(_sine(1600))
    assert audio_gate.should_transcribe(wav) is False
    # 边界：恰好低于 300ms 才 False（条件为 <300ms）；4799 帧 ≈299.9ms → False
    wav_below = _wav_bytes(_sine(4799))
    assert audio_gate.should_transcribe(wav_below) is False
    # 恰好 300ms（4800 帧）不触发（不是 <300）→ True
    wav300 = _wav_bytes(_sine(4800))
    assert audio_gate.should_transcribe(wav300) is True
    # 0.3s + 1 帧 → True
    wav301 = _wav_bytes(_sine(4801))
    assert audio_gate.should_transcribe(wav301) is True


def test_should_transcribe_non_wav_fail_open_true():
    # m4a / 任意非 RIFF 字节 → 保守回退 True（不阻塞 ASR）
    assert audio_gate.should_transcribe(b"\x1a\x45\xdf\xa3garbage-bytes-m4a") is True
    # 过短（<44）的非空字节 → 无法解析 → True
    assert audio_gate.should_transcribe(b"short") is True


def test_should_transcribe_compressed_wav_fail_open_true():
    """压缩格式（audio_format != 1，如 ADPCM=2）无法低成本解码 → 回退 True。"""
    # 手工构造一个 WAV，fmt 块 audio_format=2
    data = b"\x00" * 1000
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 2, 1, 16000, 32000, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    assert audio_gate.should_transcribe(hdr + data) is True


def test_should_transcribe_low_amplitude_silence_threshold():
    """低于静音阈值的微弱噪声（但仍为连续帧）→ False（按静音处理）。"""
    wav = _wav_bytes([1] * 16000)  # 幅度 ±1，RMS≈0.00003 << 阈值
    assert audio_gate.should_transcribe(wav) is False


def test_should_transcribe_explicit_min_duration():
    """min_duration_ms 参数可调：放宽到 50ms 后，0.1s 帧通过。"""
    wav = _wav_bytes(_sine(1600))  # 0.1s
    assert audio_gate.should_transcribe(wav, min_duration_ms=50) is True
