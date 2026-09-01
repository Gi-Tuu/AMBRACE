"""服务端轻量 VAD 门槛（P2-2，音视频帧能量/时长判定，决定是否值得转写）。

纯函数、零 LLM、零重量级依赖（仅 Python 标准库解析 WAV/PCM，不引 numpy/uss）。

- 极短帧（默认 <300ms）：不触发 ASR，省 faster-whisper；
- 全静音帧（归一化 RMS 低于阈值）：不触发 ASR；
- 格式解析受限（m4a 等压缩容器，标准库无法低成本解 PCM）→ **保守回退返回 True**
  （不阻塞 ASR：宁可多转写一次也不漏掉用户说话）。

失败/异常一律返回 True（fail-open），绝不阻塞主语音链路。
"""

from __future__ import annotations

import struct

# 默认音频假设（用于无法从 WAV 头读出时的兜底；WAV 可解析时以头为准）
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_BITS = 16

# 极短帧阈值（毫秒）
MIN_DURATION_MS = 300
# 归一化 RMS 低于该值视为静音帧（16bit 满幅=1.0；环境底噪通常 <0.01）
SILENCE_RMS_THRESHOLD = 0.01


def _parse_wav(data: bytes, sample_rate: int | None = None) -> tuple[int, int, int, int, float | None] | None:
    """尝试解析 WAV/PCM 字节。

    返回 (frame_count, sample_rate, channels, bits, normalized_rms)；
    非 WAV / 压缩格式 / 无法解析时返回 None（调用方按 fail-open 处理）。
    """
    if len(data) < 44:
        return None
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None

    audio_format: int | None = None
    raw_channels: int | None = None
    raw_rate: int | None = None
    raw_bits: int | None = None
    data_chunk: bytes | None = None

    pos = 12
    n = len(data)
    while pos + 8 <= n:
        cid = data[pos:pos + 4]
        csize = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + csize]
        pos += 8 + csize
        if cid == b"fmt " and len(body) >= 16:
            audio_format = struct.unpack("<H", body[0:2])[0]
            raw_channels = struct.unpack("<H", body[2:4])[0]
            raw_rate = struct.unpack("<I", body[4:8])[0]
            raw_bits = struct.unpack("<H", body[14:16])[0]
        elif cid == b"data":
            data_chunk = body
            break

    if data_chunk is None or data_chunk == b"":
        return None
    # 仅处理线性 PCM（format=1）；压缩格式（ADPCM=2 等）无法低成本解码 → fail-open
    if audio_format is not None and audio_format != 1:
        return None
    sr = raw_rate or sample_rate or DEFAULT_SAMPLE_RATE
    if sr <= 0:
        return None
    channels = raw_channels or DEFAULT_CHANNELS
    if channels <= 0:
        return None
    bits = raw_bits or DEFAULT_BITS
    bytes_per_sample = bits // 8
    if bytes_per_sample <= 0:
        return None
    bytes_per_frame = bytes_per_sample * channels
    if bytes_per_frame <= 0:
        return None
    frame_count = len(data_chunk) // bytes_per_frame
    rms = _rms_normalized(data_chunk, bits)
    return (frame_count, sr, channels, bits, rms)


def _rms_normalized(chunk: bytes, bits: int) -> float | None:
    """按位深解析 PCM 样本，返回归一化 RMS（满幅=1.0）；位深不支持返回 None。"""
    vals: list[int]
    denom: float
    if bits == 8:
        vals = [b - 128 for b in chunk]
        denom = 128.0
    elif bits == 16:
        vals = [struct.unpack("<h", chunk[i:i + 2])[0] for i in range(0, len(chunk) - 1, 2)]
        denom = 32768.0
    elif bits == 24:
        vals = [int.from_bytes(chunk[i:i + 3], "little", signed=True) for i in range(0, len(chunk) - 2, 3)]
        denom = 8388608.0
    elif bits == 32:
        vals = [struct.unpack("<i", chunk[i:i + 4])[0] for i in range(0, len(chunk) - 3, 4)]
        denom = 2147483648.0
    else:
        return None
    if not vals:
        return 0.0
    mean_sq = sum(v * v for v in vals) / len(vals)
    return (mean_sq ** 0.5) / denom


def should_transcribe(
    audio_bytes: bytes,
    *,
    sample_rate: int | None = None,
    min_duration_ms: int | float = MIN_DURATION_MS,
    silence_rms_threshold: float = SILENCE_RMS_THRESHOLD,
) -> bool:
    """判断这段音频帧是否值得转写。

    返回 True = 值得转写（正常帧/无法解析的保守兜底）；False = 忽略（极短或纯静音）。
    任何解析异常都 fail-open 返回 True，不阻塞 ASR。
    """
    if not audio_bytes:
        return False
    try:
        info = _parse_wav(audio_bytes, sample_rate=sample_rate)
    except Exception:
        info = None
    if info is None:
        # 非 WAV / 压缩容器（m4a 等）无法低成本解析 → 保守回退，不阻塞 ASR
        return True

    frame_count, sr, _channels, _bits, rms = info
    if sr <= 0:
        return True
    duration_ms = (frame_count / sr) * 1000.0
    if duration_ms < min_duration_ms:
        return False
    if rms is not None and rms < silence_rms_threshold:
        return False
    return True


__all__ = [
    "should_transcribe",
    "MIN_DURATION_MS",
    "SILENCE_RMS_THRESHOLD",
]
