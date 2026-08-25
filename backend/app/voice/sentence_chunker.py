"""句子切分器：LLM 流式增量 → 按标点切出完整句，剩余缓冲保留

规则：句末标点（。！？!?；;…）+ 强制长句兜底（40 字无标点也切），
保证语音 TTS 逐句合成时不会因为缺少标点而卡住整段。
"""
import re

_SENT_SPLIT = re.compile(r"([。！？!?；;…]+)")
_FORCE_LEN = 40


def _split_keep(buf: str) -> tuple[list[str], str]:
    parts = _SENT_SPLIT.split(buf)
    sentences: list[str] = []
    acc = ""
    for i, p in enumerate(parts):
        acc += p
        if i % 2 == 1:
            sentences.append(acc.strip())
            acc = ""
    return sentences, acc


class SentenceChunker:
    def __init__(self, force_len: int = _FORCE_LEN):
        self._buf = ""
        self._force_len = force_len

    def feed(self, delta: str) -> list[str]:
        """喂入增量文本，返回本轮切出的完整句子（可能为空）"""
        if not delta:
            return []
        self._buf += delta
        sentences, tail = _split_keep(self._buf)
        out = [s for s in sentences if s]
        if len(tail) >= self._force_len:
            out.append(tail.strip())
            tail = ""
        self._buf = tail
        return [s for s in out if s]

    def finish(self) -> list[str]:
        """流结束：返回残余缓冲（如有）"""
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if rest else []
