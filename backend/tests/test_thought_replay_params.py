# -*- coding: utf-8 -*-
"""A4 批 4 N1 —— ``thought_replay.py --params`` **只读模拟参数覆盖入口**的专项测试。

为什么单列一个文件
------------------
N1 要在「不改任何生产默认值」的前提下试参数，故把覆盖逻辑做成脚本内的进程级入口。
它有两处容易静默失效的坑，本文件专测这两条：

1. **早绑定陷阱**：``novelty(halflife_days=NOVELTY_HALFLIFE_DAYS)`` 与
   ``evict(cap_spark=CAP_SPARK, ...)`` 的默认参数是 **def 期求值**，只 ``setattr``
   模块属性**到不了**它们 —— 必须连同 ``__defaults__`` 一起刷新，否则「改了没反应」。
2. **权重就地污染**：``SALT_WEIGHT_BY_SOURCE`` 在 dynamics 与 extract 里是同一个 dict
   对象，覆盖必须换新 dict，绝不能 in-place 改，否则会污染生产默认值。

纪律：不连生产库、不建表、不起服务——只喂内存；每个用例退出后必须逐值还原。
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "thought_replay.py")


def _load_script():
    """按路径加载``thought_replay.py``（它不是包模块，只能 file-location 导入）。"""
    spec = importlib.util.spec_from_file_location("_replay_under_test", _SCRIPT)
    assert spec and spec.loader, "无法定位 thought_replay.py"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def replay():
    return _load_script()


# ───────────────────────── parse_params_spec ─────────────────────────

def test_parse_empty_and_none(replay):
    """空串 / None ⇒ 空 dict（= 不覆盖，逐字节旧行为）。"""
    assert replay.parse_params_spec(None) == {}
    assert replay.parse_params_spec("") == {}
    assert replay.parse_params_spec("   ") == {}


def test_parse_basic_kv(replay):
    """基本 k=v 解析，float / int 类型各自保型。"""
    got = replay.parse_params_spec("CAP_SPARK=24,NOVELTY_HALFLIFE_DAYS=3.5")
    assert got == {"CAP_SPARK": 24, "NOVELTY_HALFLIFE_DAYS": 3.5}
    assert isinstance(got["CAP_SPARK"], int)
    assert isinstance(got["NOVELTY_HALFLIFE_DAYS"], float)


def test_parse_tolerates_whitespace(replay):
    """逗号/等号周围空白容忍。"""
    got = replay.parse_params_spec(" CAP_SPARK = 20 , TTL_DAYS = 14.0 ")
    assert got == {"CAP_SPARK": 20, "TTL_DAYS": 14.0}


def test_parse_weight_key(replay):
    """来源权重用 ``W:<face>=<num>`` 形式解析出来。"""
    got = replay.parse_params_spec("W:activity=0.4")
    assert got == {"W:activity": 0.4}


def test_parse_rejects_missing_equals(replay):
    """缺 '=' ⇒ ValueError（不静默忽略半句话）。"""
    with pytest.raises(ValueError, match="缺 '='"):
        replay.parse_params_spec("CAP_SPARK:24")


def test_parse_rejects_non_numeric(replay):
    """非数字值 ⇒ ValueError（不要把字符串塞进阈值里）。"""
    with pytest.raises(ValueError, match="不是数字"):
        replay.parse_params_spec("CAP_SPARK=many")


# ───────────────────────── param_overrides ─────────────────────────

def test_override_applies_and_restores_module_attr(replay):
    """覆盖期间模块属性生效，退出**逐值还原**（不能残留到后续用例）。"""
    before = dyn.CAP_SPARK
    with replay.param_overrides({"CAP_SPARK": 999}) as eff:
        assert eff == {"CAP_SPARK": 999}
        assert dyn.CAP_SPARK == 999
    assert dyn.CAP_SPARK == before


def test_override_refreshes_novelty_bound_default(replay):
    """★ 早绑定陷阱之一：τ 必须刷进 ``novelty.__defaults__``，否则改了不生效。"""
    before_def = dyn.novelty.__defaults__
    before_val = dyn.novelty(7.0)
    with replay.param_overrides({"NOVELTY_HALFLIFE_DAYS": 3.5}):
        # 走默认参数（不带第二个实参）也必须拿到新 τ
        assert dyn.novelty(7.0) == pytest.approx(math.exp(-2.0))
        assert dyn.novelty(7.0) != before_val
    assert dyn.novelty.__defaults__ == before_def
    assert dyn.novelty(7.0) == before_val


def test_override_refreshes_evict_bound_defaults(replay):
    """★ 早绑定陷阱之二：三个 cap 必须刷进 ``evict.__defaults__``。"""
    before = dyn.evict.__defaults__
    recs = [{"id": str(i), "status": dyn.STATUS_SPARK, "salt": float(i), "novelty": 1.0}
            for i in range(10)]
    with replay.param_overrides({"CAP_SPARK": 4}):
        # 不带 cap 实参（走默认）时也必须按新顶挤出 10-4=6 条
        assert len(dyn.evict(recs)) == 6
    assert dyn.evict.__defaults__ == before
    # 还原后按原顶（12）不再挤出
    assert dyn.evict(recs) == []


def test_override_rejects_non_whitelist_key(replay):
    """白名单外的键一律拒绝 —— 防止顺手改到 _DRIFT_RULES 这类不该动的量。"""
    with pytest.raises(ValueError, match="非白名单"):
        with replay.param_overrides({"REPLY_WINDOW_MINUTES": 1}):
            pass


def test_whitelist_entries_all_exist_on_dyn(replay):
    """不变量：白名单里的每个名字都必须真的存在于 domain/thought（防「名单 vs dyn」漂移）。

    这条替代了原本写死 hasattr 的防御分支 —— 那种分支在白名单校验之后永远走不到，
    改成这里的一致性用例才能真正守住这个等幂前提。
    """
    missing = [k for k in sorted(replay._OVERRIDE_WHITELIST) if not hasattr(dyn, k)]
    assert not missing, f"白名单里有 domain/thought 中不存在的参数：{missing}"


def test_weight_override_does_not_mutate_source_dict(replay):
    """★ 不能 in-place 改 ``extract.SALT_WEIGHT_BY_SOURCE``（那会污染真实默认值）。"""
    original = dyn.SALT_WEIGHT_BY_SOURCE
    snapshot = dict(original)
    with replay.param_overrides({"W:activity": 0.4}):
        assert dyn.SALT_WEIGHT_BY_SOURCE["activity"] == 0.4
        assert ex.SALT_WEIGHT_BY_SOURCE["activity"] == snapshot["activity"], "extract 字典被污染了"
        assert dyn.SALT_WEIGHT_BY_SOURCE is not original, "应换新 dict，而非就地改"
    assert dyn.SALT_WEIGHT_BY_SOURCE is original, "退出后应还原成原对象"
    assert dict(dyn.SALT_WEIGHT_BY_SOURCE) == snapshot


def test_weight_override_feeds_salt_of(replay):
    """权重覆盖要真的进到 ``salt_of``（这是它唯一的意义）。"""
    baseline = dyn.salt_of(["activity"])
    with replay.param_overrides({"W:activity": 0.4}):
        assert dyn.salt_of(["activity"]) == pytest.approx(0.4)
    assert dyn.salt_of(["activity"]) == baseline


def test_unknown_weight_face_raises(replay):
    """未知来源面 ⇒ 报错，而不是静默塞进字典。"""
    with pytest.raises(ValueError, match="未知来源面"):
        with replay.param_overrides({"W:nonsense": 1.0}):
            pass


def test_no_override_is_noop(replay):
    """不传覆盖 ⇒ 完全不动任何东西（保证「不加 --params 就是逐字节旧行为」）。"""
    cap_before, def_before = dyn.CAP_SPARK, dyn.evict.__defaults__
    with replay.param_overrides({}) as eff:
        assert eff == {}
        assert dyn.CAP_SPARK == cap_before
        assert dyn.evict.__defaults__ is def_before
    assert dyn.CAP_SPARK == cap_before
