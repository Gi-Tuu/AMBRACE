"""数值钳制工具：跨模块共享的状态值裁剪（0-100）"""


def clamp_int(value, default=None, lo: int = 0, hi: int = 100) -> int:
    """转 int 并裁剪到 [lo, hi]。

    转换失败时返回 default；default 为 None 时抛出原始 TypeError/ValueError。
    user_states 的健壮兜底（default=50）与 life_home/pet_service 的严格模式共用此实现。
    """
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise
    return max(lo, min(hi, iv))
