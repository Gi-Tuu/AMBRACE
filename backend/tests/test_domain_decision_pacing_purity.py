# -*- coding: utf-8 -*-
"""domain 去 IO 铺开 V2b（架构地图断点 #1）：decision/layer.py + proactivity/pacing.py 纯度测试。

守的三条底线（禁止为绿放宽）：
1. **零行为**：三闸阈值/判定顺序/灰度桶、影子留痕的字段与缓冲三常数，在假端口 + 假时钟下
   与 HEAD 旧实现逐例等价（本文件内的 ``_legacy_*`` 是 git show HEAD 旧函数体的逐字拷贝，
   作为对照 oracle；两侧结果必须相同）；
2. **未注入不静默降级**：端口拿不到时抛 PacingPortsNotInjected / ShadowPortsNotInjected，
   不表现为「开关恒关」；
3. **domain 不再跨层取 IO**：两个 domain 文件里不得再出现 AGENT_FLAGS / time / ORM / trace 的 import。

全部用例走假端口，零 DB、零事件循环依赖（需要 running loop 的批量落库用例用 asyncio.run）。
"""
import ast
import asyncio
import json
import pathlib

import pytest

from app.domain.decision import layer as dl
from app.domain.decision.ports import ShadowPortsNotInjected
from app.domain.proactivity import pacing
from app.domain.proactivity.ports import PacingPortsNotInjected

_DOMAIN_FILES = {
    "layer.py": pathlib.Path(dl.__file__),
    "pacing.py": pathlib.Path(pacing.__file__),
}


# ═══════════════ 旧实现 oracle（git show HEAD 的函数体逐字拷贝） ═══════════════

def _legacy_flag_on(key, *, flags=None):
    """旧 pacing.flag_on（HEAD:backend/app/domain/proactivity/pacing.py:118-129）。"""
    if flags is None:
        try:
            from app.agent.loop import AGENT_FLAGS
            flags = AGENT_FLAGS
        except Exception:
            return False
    try:
        return bool(flags.get(key, False))
    except Exception:
        return False


def _legacy_gate_active(character_id, session_id, key, *, flags=None,
                       chars=pacing.OUTREACH_PACING_GRAY_CHARS,
                       ratio=pacing.OUTREACH_PACING_RATIO):
    """旧 pacing.gate_active（HEAD 同文件:132-144）。"""
    if not _legacy_flag_on(key, flags=flags):
        return False
    return pacing.pacing_gray_hit(character_id, session_id, chars=chars, ratio=ratio)


def _legacy_row_kwargs(record, *, character_id, user_id, latency_ms, task_id):
    """旧 layer._row_kwargs（HEAD:backend/app/domain/decision/layer.py:161-171）。"""
    return {
        "task_id": task_id,
        "character_id": character_id,
        "user_id": user_id,
        "trigger": dl.SHADOW_TRIGGER,
        "route": dl.SHADOW_ROUTE,
        "steps_json": json.dumps(record, ensure_ascii=False, default=str)[:1600],
        "latency_ms": latency_ms,
        "status": "ok",
    }


# ═══════════════ 假端口 ═══════════════

class FakePacingPorts:
    """只实现 PacingPorts.flags()：pacing 真正调用的 IO 就这一项。"""

    def __init__(self, flags):
        self._flags = flags
        self.calls = 0

    def flags(self):
        self.calls += 1
        return self._flags


class FakeShadowPorts:
    """DecisionShadowPorts 假实现：假时钟（perf 每步 +2ms、monotonic 由测试推进）+ 假出口。"""

    def __init__(self, flags=None, *, monotonic=1000.0, owners=None):
        self._flags = {} if flags is None else flags
        self._monotonic = monotonic
        self._perf = 0.0
        self._seq = 0
        self.calls = []
        self.enqueued = []
        self.spawned = []
        self.rows = []
        self.owners = dict(owners or {})
        self.spawn_error = None
        self.write_error = None
        self.flags_error = None

    # ── 开关与时钟 ──
    def flags(self):
        self.calls.append("flags")
        if self.flags_error is not None:
            raise self.flags_error
        return self._flags

    def perf_counter(self):
        self.calls.append("perf_counter")
        value = self._perf
        self._perf += 0.002          # 每步 2ms ⇒ latency_ms 恒为 2
        return value

    def monotonic(self):
        self.calls.append("monotonic")
        return self._monotonic

    def advance(self, seconds):
        self._monotonic += seconds

    # ── direct 通道 ──
    def new_task_id(self):
        self.calls.append("new_task_id")
        self._seq += 1
        return f"shadow{self._seq:07d}"

    def enqueue_task_log(self, **row):
        self.calls.append("enqueue_task_log")
        self.enqueued.append(row)

    # ── buffer 通道 ──
    def spawn_background(self, coro, *, name=None):
        self.calls.append("spawn_background")
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append((coro, name))
        return coro

    async def resolve_owner_user_id(self, character_id):
        self.calls.append("resolve_owner_user_id")
        return self.owners.get(character_id)

    async def write_shadow_rows(self, rows):
        self.calls.append("write_shadow_rows")
        if self.write_error is not None:
            raise self.write_error
        self.rows.extend(rows)


@pytest.fixture(autouse=True)
def _clean_shadow_state():
    dl.reset_shadow_state()
    dl.set_default_shadow_ports(None)
    pacing.set_default_pacing_ports(None)
    yield
    dl.reset_shadow_state()
    dl.set_default_shadow_ports(None)
    pacing.set_default_pacing_ports(None)


# ═══════════════ ① pacing：端口只有一项，判定与旧实现逐例等价 ═══════════════

def test_pacing_开关判定与旧实现逐例等价():
    cases = [
        ({}, pacing.FLAG_HOUR_WINDOW),
        ({pacing.FLAG_HOUR_WINDOW: True}, pacing.FLAG_HOUR_WINDOW),
        ({pacing.FLAG_HOUR_WINDOW: False}, pacing.FLAG_HOUR_WINDOW),
        ({pacing.FLAG_HOUR_WINDOW: 1}, pacing.FLAG_HOUR_WINDOW),
        ({pacing.FLAG_HOUR_WINDOW: 0}, pacing.FLAG_HOUR_WINDOW),
        ({pacing.FLAG_TYPE_MIX: True}, pacing.FLAG_HOUR_WINDOW),   # 串键不误命中
        ({pacing.FLAG_SESSION_RATE: "yes"}, pacing.FLAG_SESSION_RATE),
    ]
    for flags, key in cases:
        ports = FakePacingPorts(flags)
        assert pacing.flag_on(key, ports=ports) == _legacy_flag_on(key, flags=flags)
        assert ports.calls == 1, "开关判定最多取一次开关表（不多不少）"
    # 端口取表内部失败 ⇒ 与旧实现同样 fail-safe 到 False（走旧路径）
    boom = FakePacingPorts({})
    boom.flags_error = RuntimeError("flags 源炸了")
    assert pacing.flag_on(pacing.FLAG_TYPE_MIX, ports=boom) is False
    assert pacing.flag_on(pacing.FLAG_TYPE_MIX, flags="不是映射") is False   # 旧口径：异常→False


def test_pacing_显式flags时完全不碰端口():
    ports = FakePacingPorts({pacing.FLAG_TYPE_MIX: True})
    assert pacing.gate_active(13, 1, pacing.FLAG_TYPE_MIX, flags={pacing.FLAG_TYPE_MIX: True},
                              ports=ports) is True
    assert ports.calls == 0, "纯判定路径（调用方已给 flags）不得触发任何 IO"


def test_pacing_gate_active_开关乘灰度矩阵与旧实现等价():
    for key in pacing.PACING_FLAGS:
        for on in (False, True):
            for cid, sid in ((13, 1), (13, None), (18, 1), (13, 7), (None, 1), ("x", 1), (0, 1)):
                flags = {key: on}
                ports = FakePacingPorts(flags)
                got = pacing.gate_active(cid, sid, key, ports=ports)
                assert got == _legacy_gate_active(cid, sid, key, flags=flags), (key, on, cid, sid)


def test_pacing_三闸阈值与集合常数逐字不变():
    assert (pacing.FLAG_HOUR_WINDOW, pacing.FLAG_TYPE_MIX, pacing.FLAG_SESSION_RATE) == (
        "outreach_hour_window_v1", "outreach_type_mix_v1", "outreach_session_rate_v1")
    assert pacing.PACING_FLAGS == ("outreach_hour_window_v1", "outreach_type_mix_v1",
                                   "outreach_session_rate_v1")
    assert pacing.OUTREACH_PACING_GRAY_CHARS == frozenset({13})
    assert pacing.OUTREACH_PACING_RATIO == 1.0
    assert pacing.LOW_YIELD_TYPES == frozenset({
        "ai_care", "life_regression", "memory_review", "memory_review_contextual"})
    assert (pacing.HOUR_WINDOW_START, pacing.HOUR_WINDOW_END) == (12, 23)
    assert pacing.TYPE_DAILY_LIMITS == {"memory_review": 6, "ai_care": 4}
    assert pacing.TYPE_MIX_COUNTED_TYPES == {
        "memory_review": "memory_review",
        "memory_review_contextual": "memory_review",
        "ai_care": "ai_care",
    }
    assert (pacing.SESSION_DAILY_LIMIT, pacing.SESSION_MIN_INTERVAL_MINUTES) == (8, 45)
    assert pacing.SESSION_RATE_TYPES == frozenset({
        "greeting", "proactive_chat", "goodnight", "status_update",
        "memory_review", "memory_review_contextual", "emotion_care", "pet_remind",
        "ai_care", "life_regression", "motivation", "unfinished_topic"})
    assert pacing.SESSION_RATE_EXEMPT_TYPES == frozenset({
        "plugin", "state_trigger", "prospective_intent"})


def test_pacing_每日上限与每小时额度边界值():
    # ② 类型配比：memory_review ≤6、ai_care ≤4（边界：等于上限即拦）
    for sent, want in ((0, True), (5, True), (6, False), (7, False)):
        assert pacing.type_mix_allows("memory_review", sent) is want
    for sent, want in ((3, True), (4, False), (99, False)):
        assert pacing.type_mix_allows("ai_care", sent) is want
    assert pacing.type_mix_allows("memory_review_contextual", 6) is False   # 合并计数
    assert pacing.type_mix_allows("greeting", 999) is True                  # 无上限类型恒放行
    # ③ 单会话：日上限 8（与 MAX_PER_HOUR 叠加、不替换）
    for sent, want in ((7, True), (8, False), (None, True), (0, True)):
        assert pacing.session_rate_allows(sent, None) is want
    # ③ 最小间隔 45 分钟：恰好 45 放行（半开区间）
    for minutes, want in ((44.9999, False), (45.0, True), (46, True), (0, False)):
        assert pacing.session_rate_allows(0, minutes) is want
    assert pacing.session_rate_allows(8, 999.0) is False    # 日上限优先于间隔
    # ① 时段窗口：[12, 23) 含 12 不含 23
    for hour, want in ((3, False), (11, False), (12, True), (18, True), (22, True), (23, False)):
        assert pacing.hour_window_allows("ai_care", hour) is want
    assert pacing.hour_window_allows("plugin", 3) is True   # 非低效类型恒放行
    # 个性化活跃时段只扩不缩（含跨天区间 / 非法数据回退）
    assert pacing.hour_window_allows("ai_care", 9, active_hours=[[8, 11]]) is True
    assert pacing.hour_window_allows("ai_care", 1, active_hours=[[22, 2]]) is True
    assert pacing.hour_window_allows("ai_care", 3, active_hours=[[8, 11]]) is False
    assert pacing.hour_window_allows("ai_care", 20, active_hours=[[8, 11]]) is True
    assert pacing.hour_window_allows("ai_care", 9, active_hours="bad") is False


def test_pacing_灰度白名单与比例桶边界():
    assert pacing.pacing_gray_hit(13) is True
    assert pacing.pacing_gray_hit(18) is False
    assert pacing.pacing_gray_hit(None) is False
    assert pacing.pacing_gray_hit("x") is False
    assert pacing.pacing_gray_hit("13") is True            # int() 兜得住就按 13 算
    assert pacing.pacing_gray_hit(13, chars=frozenset()) is True        # 空白名单 = 全量
    assert pacing.pacing_gray_hit(18, chars=frozenset()) is True
    assert pacing.pacing_gray_hit(13, ratio=0.0) is False
    assert pacing.pacing_gray_hit(13, chars=frozenset(), ratio=0.0) is False
    assert pacing.pacing_gray_hit(13, ratio=1.0) is True
    assert pacing.pacing_gray_hit(13, ratio=2.0) is True
    # 分桶键逐字为 pacing:<cid>:<session|0>，比例 (0,1) 时与 md5 桶号一致
    for cid, sid in ((13, 1), (13, None), (18, 7), (999, 3)):
        key = f"pacing:{cid}:{sid if sid is not None else 0}"
        bucket = int(__import__("hashlib").md5(key.encode("utf-8")).hexdigest()[:8], 16) % 1000
        for ratio in (0.001, 0.3, 0.5, 0.999):
            assert pacing.pacing_gray_hit(cid, sid, chars=frozenset(), ratio=ratio) is (
                bucket < int(round(ratio * 1000)))
    assert pacing.traffic_hit("k", 0) is False and pacing.traffic_hit("k", 1) is True


def test_pacing_默认端口绑定生产实现_读的还是同一张AGENT_FLAGS(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setattr(pacing, "_DEFAULT_PORTS", None)
    old = AGENT_FLAGS.get(pacing.FLAG_SESSION_RATE, None)
    AGENT_FLAGS[pacing.FLAG_SESSION_RATE] = True
    try:
        assert pacing.flag_on(pacing.FLAG_SESSION_RATE) is True       # 与旧实现同表同值
        assert pacing._DEFAULT_PORTS is not None                      # 惰性绑定后缓存
    finally:
        if old is None:
            AGENT_FLAGS.pop(pacing.FLAG_SESSION_RATE, None)
        else:
            AGENT_FLAGS[pacing.FLAG_SESSION_RATE] = old


def test_pacing_未注入端口即抛清晰错误_不静默降级(monkeypatch):
    """端口一处都拿不到时：抛 PacingPortsNotInjected，绝不退化成「开关看起来永远是关」。"""
    monkeypatch.setattr(pacing, "_DEFAULT_PORTS", None)

    def _broken():
        raise ImportError("app.application.proactivity_pacing_ports 不存在")
    monkeypatch.setattr(pacing, "_bind_production_ports", _broken)

    with pytest.raises(PacingPortsNotInjected) as exc:
        pacing.flag_on(pacing.FLAG_TYPE_MIX)
    assert "PacingPorts" in str(exc.value) and "断点 #1" in str(exc.value)
    with pytest.raises(PacingPortsNotInjected):
        pacing.gate_active(13, 1, pacing.FLAG_TYPE_MIX)
    # 显式传 flags 的纯判定路径不受影响（零 IO）
    assert pacing.gate_active(13, 1, pacing.FLAG_TYPE_MIX,
                              flags={pacing.FLAG_TYPE_MIX: True}) is True


# ═══════════════ ② decision layer：三原语透传 + 影子留痕 ═══════════════

def test_layer_开关关_只取一次开关表_返回值与旧实现逐字相同():
    obj = object()
    ports = FakeShadowPorts({dl.FLAG_KEY: False})
    assert dl.ask_noul("s", "q", legacy=lambda: True, ports=ports) == (True, None)
    assert dl.ask_choice("s", "q", ("a", "b"), legacy=lambda: "b", ports=ports) == ("b", None)
    assert dl.ask_score("s", "q", 1, 5, legacy=lambda: 4, ports=ports) == (4, None)
    got, conf = dl.ask_choice("s", "q", (), legacy=lambda: obj, ports=ports)
    assert got is obj and conf is None                      # 透传同一对象，不复制不改类型
    dl.observe_tense_decision({"content": "明天去长沙"}, "plan", ports=ports)
    assert dl.shadow_buffer_size() == 0
    assert ports.enqueued == [] and ports.spawned == [] and ports.rows == []
    assert ports.calls == ["flags"] * 5, "关态下每个原语只碰开关表，不起计时器、不碰写库通道"


def test_layer_开关开_direct留痕行与旧实现逐字相同():
    record = {
        "primitive": "score",
        "hook": "memory_star_rating",
        "state": "用户说明天要去长沙",
        "question": "这条记忆有多重要",
        "output": 4,
        "confidence": None,
        "latency_ms": 2,                                    # 假时钟：一次 perf 步长 2ms
        "source": "legacy",
        "lo": 1,
        "hi": 5,
        "output_in_range": True,
        "context": {"memory_id": 11, "raw_star": 9},
    }
    expected = _legacy_row_kwargs(record, character_id=13, user_id=1, latency_ms=2,
                                  task_id="shadow0000001")
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    out = dl.ask_score("用户说明天要去长沙", "这条记忆有多重要", 1, 5, legacy=lambda: 4,
                       hook="memory_star_rating", character_id=13, user_id=1,
                       context={"memory_id": 11, "raw_star": 9}, ports=ports)
    assert out == (4, None)
    assert ports.enqueued == [expected]
    assert ports.rows == [] and ports.spawned == []          # direct 形态不进缓冲、不起批任务
    assert json.loads(expected["steps_json"])["confidence"] is None   # 禁止编造成 0/1


def test_layer_choice候选留痕与生成器物化口径不变():
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    options = (f"opt{i}" for i in range(20))                 # 生成器：必须先物化再判 in
    value, _ = dl.ask_choice("明天去长沙", "memory_tense", options, legacy=lambda: "opt1",
                             hook="memory_tense", ports=ports)
    assert value == "opt1"
    steps = json.loads(ports.enqueued[0]["steps_json"])
    assert steps["options"] == [f"opt{i}" for i in range(12)]   # 只留前 12 条
    assert steps["output_in_options"] is True
    assert steps["primitive"] == "choice" and steps["output"] == "opt1"
    # 不在候选里也照原样交回（阶段 0 不校验、不改选）
    ports2 = FakeShadowPorts({dl.FLAG_KEY: True})
    assert dl.ask_choice("s", "q", ("a", "b"), legacy=lambda: "zz", ports=ports2)[0] == "zz"
    assert json.loads(ports2.enqueued[0]["steps_json"])["output_in_options"] is False
    # 超长候选逐条截 40、hook 缺省为 unknown
    ports3 = FakeShadowPorts({dl.FLAG_KEY: True})
    dl.ask_noul("x" * 200, "q", legacy=lambda: 1, sink=dl.SINK_DIRECT, ports=ports3)
    steps3 = json.loads(ports3.enqueued[0]["steps_json"])
    assert "options" not in steps3 and steps3["hook"] == "unknown"
    assert len(steps3["state"]) == dl._STATE_MAX == 120
    assert steps3["output"] == 1                              # 非 str 也原样留痕（_jsonable 直通）


def test_layer_score区间不可比时output_in_range为None():
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    assert dl.ask_score("s", "q", 1, 5, legacy=lambda: "abc", ports=ports) == ("abc", None)
    steps = json.loads(ports.enqueued[0]["steps_json"])
    assert steps["output_in_range"] is None and steps["output"] == "abc"
    assert steps["lo"] == 1 and steps["hi"] == 5
    assert json.loads(ports.enqueued[0]["steps_json"])["confidence"] is None


def test_layer_legacy异常两态照常向外抛且零留痕():
    def _boom():
        raise ValueError("业务异常不得被观测层吞掉")
    for flag in (False, True):
        ports = FakeShadowPorts({dl.FLAG_KEY: flag})
        with pytest.raises(ValueError):
            dl.ask_score("s", "q", 1, 5, legacy=_boom, hook="memory_star_rating", ports=ports)
        assert ports.enqueued == [] and ports.spawned == [] and ports.rows == []
        assert dl.shadow_buffer_size() == 0


def test_layer_buffer假时钟攒满25条才整批交出_调用点不写库():
    ports = FakeShadowPorts({dl.FLAG_KEY: True}, monotonic=500.0)

    async def _go():
        for i in range(dl._BUFFER_FLUSH_COUNT - 1):
            dl.observe_tense_decision({"content": f"下周出差{i}", "memory_type": "event"},
                                      "plan", character_id=13, user_id=1, ports=ports)
            assert ports.rows == []                           # 调用点全程没有写库
        assert dl.shadow_buffer_size() == dl._BUFFER_FLUSH_COUNT - 1
        assert ports.spawned == []                            # 差一条不触发攒批
        dl.observe_tense_decision({"content": "下周出差最后一批", "memory_type": "event"},
                                  "plan", character_id=13, user_id=1, ports=ports)
        assert dl.shadow_buffer_size() == 0                   # 第 25 条 ⇒ 整批交出
        assert len(ports.spawned) == 1
        coro, name = ports.spawned[0]
        assert name == "decision_shadow_batch"
        assert ports.rows == []                               # 没被 await 前一行都不写
        await coro
        return len(ports.rows)
    written = asyncio.run(_go())
    assert written == dl._BUFFER_FLUSH_COUNT == 25            # 一决策一行
    assert all(r["route"] == dl.SHADOW_ROUTE and r["trigger"] == dl.SHADOW_TRIGGER
               and r["status"] == "ok" and r["character_id"] == 13 and r["user_id"] == 1
               and r["latency_ms"] == 2 for r in ports.rows)
    hooks = {json.loads(r["steps_json"])["hook"] for r in ports.rows}
    assert hooks == {"memory_tense"}
    assert all(json.loads(r["steps_json"])["context"]["via"] == "rule" for r in ports.rows)


def test_layer_buffer假时钟按年龄120秒触发_119点9不触发():
    assert dl._BUFFER_FLUSH_AGE_S == 120.0 and dl._BUFFER_FLUSH_COUNT == 25
    for age, want_flush in ((119.9, False), (120.0, True)):
        dl.reset_shadow_state()
        ports = FakeShadowPorts({dl.FLAG_KEY: True}, monotonic=1000.0)

        async def _go():
            dl.observe_tense_decision({"content": "明天去长沙"}, "plan", ports=ports)  # 起表
            assert dl.shadow_buffer_size() == 1
            assert ports.spawned == []
            ports.advance(age)
            dl.observe_tense_decision({"content": "后天去长沙"}, "plan", ports=ports)
            return dl.shadow_buffer_size()
        size = asyncio.run(_go())
        for coro, _n in ports.spawned:                        # 别让协程悬着（never awaited）
            if not coro.cr_running:
                coro.close()
        assert (len(ports.spawned) == 1) is want_flush       # 半开区间：>=120.0 才刷
        assert size == (0 if want_flush else 2)
        assert ports.calls.count("monotonic") == 2           # 两条各一次，成功交出不再补计时


def test_layer_无事件循环时整批留在缓冲且不做阻塞式写库():
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    for _ in range(3):
        dl.observe_tense_decision({"content": "明天去长沙"}, "plan", ports=ports)
    assert dl.shadow_buffer_size() == 3
    assert ports.rows == [] and ports.spawned == []           # 一次写库都没发生
    assert dl.flush_shadow_buffer(ports=ports) == 0           # 无循环时明确交不出去
    assert dl.shadow_buffer_size() == 3
    assert ports.calls.count("monotonic") == 4                # 三条各一次 + 空转重置一次


def test_layer_交不出去时关掉协程并把整批回灌缓冲头部():
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    ports.spawn_error = RuntimeError("调度失败")

    async def _go():
        for i in range(2):
            dl.observe_tense_decision({"content": f"明天去长沙{i}"}, "plan", ports=ports)
        assert dl.flush_shadow_buffer(ports=ports) == 0
        return dl.shadow_buffer_size(), dl.shadow_dropped_total()
    size, dropped = asyncio.run(_go())
    assert size == 2                                          # 整批回到缓冲，没丢
    assert dropped == 0
    states = [item["record"]["state"] for item in dl._shadow_buffer]
    assert states == ["明天去长沙0", "明天去长沙1"]            # 顺序不变（回灌到头部）
    assert ports.rows == []


def test_layer_requeue超硬上限时丢最旧并累计dropped():
    """硬上限（200）分支的逐字口径：只留 batch 尾部 200 条、丢的条数计入 dropped、起表计时。"""
    assert dl._BUFFER_HARD_CAP == 200
    ports = FakeShadowPorts({dl.FLAG_KEY: True}, monotonic=70.0)
    batch = [{"record": {"state": f"c{i}"}, "character_id": None, "user_id": 1,
              "latency_ms": 0} for i in range(205)]
    dl._requeue(batch, ports=ports)
    assert dl.shadow_buffer_size() == 200
    assert dl.shadow_dropped_total() == 5
    assert [item["record"]["state"] for item in dl._shadow_buffer] == [f"c{i}" for i in range(5, 205)]
    assert dl._shadow_buffer_since == 70.0
    dl._requeue([{"record": {"state": "x"}, "user_id": 1, "latency_ms": 0}], ports=ports)
    assert dl._shadow_buffer_since == 70.0                    # 已计时则不重置（旧口径）
    assert dl.shadow_dropped_total() == 5


def test_layer_批量落库缺user_id时按角色补归属并逐行新生成task_id():
    ports = FakeShadowPorts({dl.FLAG_KEY: True}, owners={13: 777})
    batch = [{"record": {"primitive": "choice"}, "character_id": 13, "user_id": None,
              "latency_ms": 5},
             {"record": {"primitive": "noul"}, "character_id": None, "user_id": 42,
              "latency_ms": 0}]
    asyncio.run(dl._write_batch(batch, ports=ports))
    assert len(ports.rows) == 2
    assert ports.rows[0]["user_id"] == 777 and ports.rows[1]["user_id"] == 42
    assert ports.rows[0]["task_id"] != ports.rows[1]["task_id"]   # 一决策一个新 id
    assert ports.rows[1]["latency_ms"] == 0
    assert ports.calls.count("resolve_owner_user_id") == 1        # 有 user_id 时不解析
    assert ports.calls.count("new_task_id") == 2


def test_layer_批量写库失败只WARNING不影响判定_属fail_open():
    ports = FakeShadowPorts({dl.FLAG_KEY: True})
    ports.write_error = RuntimeError("db down")

    async def _go():
        for i in range(dl._BUFFER_FLUSH_COUNT):
            dl.observe_tense_decision({"content": f"明天去长沙{i}"}, "plan", ports=ports)
        assert dl.shadow_buffer_size() == 0                   # 批次已交出，不回灌死循环
        for coro, _name in ports.spawned:
            await coro                                        # 写失败被吞，不外抛
    asyncio.run(_go())
    assert ports.rows == []                                   # 一条都没落
    assert dl.shadow_buffer_size() == 0


def test_layer_未注入端口即抛清晰错误_不静默降级(monkeypatch):
    """端口一处都拿不到时抛 ShadowPortsNotInjected：不得伪装成「影子开关是关的」。"""
    monkeypatch.setattr(dl, "_DEFAULT_PORTS", None)

    def _broken():
        raise ImportError("app.application.decision_layer_ports 不存在")
    monkeypatch.setattr(dl, "_bind_production_ports", _broken)

    with pytest.raises(ShadowPortsNotInjected) as exc:
        dl.shadow_enabled()
    assert "DecisionShadowPorts" in str(exc.value) and "断点 #1" in str(exc.value)
    with pytest.raises(ShadowPortsNotInjected):
        dl.ask_score("s", "q", 1, 5, legacy=lambda: 4)
    # legacy() 一次都没被调用（不是「吞掉端口错误后照常返回」）
    called = []
    with pytest.raises(ShadowPortsNotInjected):
        dl.ask_noul("s", "q", legacy=lambda: called.append(1))
    assert called == []


# ═══════════════ ③ 结构约束：两个 domain 文件不再跨层取 IO ═══════════════

_ALLOWED_APP_IMPORTS = {
    "app.domain.proactivity.ports", "app.domain.decision.ports", "app.utils.logger",
    "app.application.proactivity_pacing_ports", "app.application.decision_layer_ports",
    "app.memory", "app.memory.tense",
}


def _app_imports(path):
    """返回 (顶层 app import 列表, 全部 app import 列表)——只看真 import 语句，注释不算。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    top, every = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top.extend(m for m in _modules_of(node) if m.startswith("app."))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            every.extend(m for m in _modules_of(node) if m.startswith("app."))
    return top, every


def _modules_of(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module or ""]


def test_domain两文件不再跨层取IO_且时钟与AGENT_FLAGS已出文件():
    for name, path in _DOMAIN_FILES.items():
        top, every = _app_imports(path)
        assert set(top) <= {"app.domain.proactivity.ports", "app.domain.decision.ports",
                            "app.utils.logger"}, f"{name} 顶层越界 import: {top}"
        assert set(every) <= _ALLOWED_APP_IMPORTS, f"{name} 越界 import: {set(every) - _ALLOWED_APP_IMPORTS}"
        for module in every:
            assert not module.startswith(("app.agent", "app.db", "app.models", "app.scheduling",
                                          "app.api", "app.flags", "app.utils.async_tasks")), \
                f"{name} 仍在取 {module}"
    layer_path = _DOMAIN_FILES["layer.py"]
    _top, every = _app_imports(layer_path)
    assert "app.agent.loop" not in every and "app.agent.trace" not in every
    assert "app.db.database" not in every and "app.models.agent" not in every
    assert "app.utils.async_tasks" not in every
    # 时钟已从本文件消失（time.perf_counter / time.monotonic 改由端口提供）
    tree = ast.parse(layer_path.read_text(encoding="utf-8"), filename=layer_path.name)
    assert "time" not in [alias.name for node in tree.body if isinstance(node, ast.Import)
                          for alias in node.names]


def test_domain端口模块自身零业务依赖():
    import importlib
    for name in ("app.domain.decision.ports", "app.domain.proactivity.ports"):
        path = pathlib.Path(importlib.import_module(name).__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in _modules_of(node):
                    if module == "__future__":
                        continue
                    assert module.split(".")[0] == "typing", f"{path.name} 只许依赖 typing，实际 {module}"
