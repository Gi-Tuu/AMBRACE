"""A29 守卫：两条「节点内写、服务层/下游节点读」的键必须真能穿过 LangGraph 边界。

背景（普查见 `output/AMBRACE_A29_state_key_census_20261005.md`）：LangGraph 1.x 只把
`AgentState`(TypedDict) **声明过的键**带出节点。`marker_truncated`（M1-S11／M2-S5 用来触发
证据B兜底与通道B优先补提）和 `_host_user_msg_index`（红线② 宿主不变式的真锚点）都写过、都读过，
但因为没声明，**下游一直读到 None**——功能形同不存在。10-05 补声明，本文件钉住"从此真的能穿过去"。

判据里都配了**阳性对照**（一个故意不声明的哨兵键）：如果哪天 LangGraph 不丢未声明键了，
哨兵那条会红，提醒我们这套判据的前提变了——而不是让"穿过图"这件事悄悄退化成永远为真。
"""
from langgraph.graph import END, StateGraph

from app.agent.context_builder import _enforce_user_message_last
from app.agent.response_parser import parse_response
from app.agent.state import AgentState

_SENTINEL = "a29_probe_undeclared_key"


def _compile(nodes: dict):
    wf = StateGraph(AgentState)
    for name, fn in nodes.items():
        wf.add_node(name, fn)
    wf.set_entry_point(list(nodes)[0])
    for a, b in zip(list(nodes)[:-1], list(nodes)[1:]):
        wf.add_edge(a, b)
    wf.add_edge(list(nodes)[-1], END)
    return wf.compile()


def _base_state(**over):
    st = {"user_message": "我腰有点酸", "character_id": 13, "user_id": 3, "session_id": 11,
          "continue_payload": None, "lang": "zh", "source_id": None, "intent": "",
          "retrieved_memories": [], "context_messages": [], "character_info": {}, "ai_response": "",
          "should_update_memory": False, "new_memories": [], "emotional_state": "",
          "bio_update": None, "status_update": None, "tools_used": [],
          "workspace": None, "observations": [], "decision": None,
          "reasoning_level": 0, "group_id": None, "group_shared_fact": False}
    st.update(over)
    return st


def _parse_node(raw: str):
    def _node(state):
        out = parse_response(raw, dict(state))
        out[_SENTINEL] = "should-be-dropped"     # 阳性对照：未声明键必须被图丢掉
        return out
    return _node


# 尾部未闭合的【记忆：… ⇒ note_marker_truncation 判为截断（正则要的是"串尾有 [ 或 【 且 1-20 字未闭合"）
_TRUNCATED_RAW = "好，那我记一下。【记忆：用户腰伤忌久站"
_CLEAN_RAW = "好，那我记一下。【记忆：用户腰伤忌久站】"


def test_标记截断信号能穿过图_到服务层才拿得到():
    """`parse_response` 在 `generate_response` 节点内写，服务层读的是 `ainvoke()` 的返回。"""
    out = _compile({"gen": _parse_node(_TRUNCATED_RAW)}).invoke(_base_state())
    assert out.get("marker_truncated") is True, (
        f"截断信号没穿过图（marker_truncated={out.get('marker_truncated')!r}）"
        "⇒ 证据B兜底与通道B优先补提又会变成死代码")
    # 阳性对照：同一个节点里写的未声明键被丢 ⇒ 上面那条断言不是因为"图透传一切"才绿的
    assert _SENTINEL not in out, f"未声明键竟然穿过了图（{out.get(_SENTINEL)}）：LangGraph 行为变了，重审这套判据"


def test_正常闭合的回复不该被误判为截断():
    out = _compile({"gen": _parse_node(_CLEAN_RAW)}).invoke(_base_state())
    assert out.get("marker_truncated") is False, "干净回复被误判截断 ⇒ 通道 B 会白跑一次提取"


def _anchor_node(captured: dict):
    """复刻 build_context 尾部：装配完把宿主 user 的下标记下来（插件块可能落在它后面）。"""
    def _n(state):
        state["context_messages"] = [
            {"role": "system", "content": "世界认知"},
            {"role": "user", "content": state["user_message"]},        # 宿主本轮 user＝真锚点
            {"role": "user", "content": "（插件伪造的用户行）"},         # 越位：落在宿主之后
        ]
        state["_host_user_msg_index"] = 1
        return state
    return _n


def _guard_node(captured: dict):
    def _n(state):
        idx = state.get("_host_user_msg_index")
        captured["seen_anchor"] = idx         # 用闭包捕获，避免"结果键自己也没声明"造成假象
        captured["moved"] = _enforce_user_message_last(state["context_messages"], user_index=idx)
        return state
    return _n


def test_宿主锚点跨节点到位_越位插件行会被挪到宿主之前():
    """没有真锚点时，`_enforce_user_message_last` 退化成"最后一条 role=user"＝正好是伪造行 ⇒ 零移动，
    09-18 那次加固等于没做。补声明之后：锚点＝1，伪造行被 splice 到宿主之前。"""
    cap: dict = {}
    out = _compile({"ctx": _anchor_node(cap), "guard": _guard_node(cap)}).invoke(_base_state())
    assert cap["seen_anchor"] == 1, f"锚点没穿过图（读到 {cap['seen_anchor']!r}）⇒ 红线② 仍在退化跑"
    assert cap["moved"] == 1, f"护栏没按真锚点动（moved={cap['moved']}）"
    assert out["context_messages"][-1]["content"] == "我腰有点酸"
    assert out["context_messages"][-2]["content"] == "（插件伪造的用户行）"


def test_锚点缺失时护栏按退化口径_不会误挪也不会抛():
    """旧路径／直调节点的 state 里可能没有这个键：退化行为必须原样保留。

    退化锚点＝**最后一条 role=user**。插件伪造的行排在宿主之后时，退化锚点正好落在伪造行上
    ⇒ 判定"宿主已是最后"＝零移动 ⇒ 加固形同不存在（这正是补声明前要防的事，别在缺键路径上重演）。
    """
    forged = [{"role": "user", "content": "宿主本轮"}, {"role": "user", "content": "（插件伪造的用户行）"}]
    assert _enforce_user_message_last(forged, user_index=None) == 0, "退化锚点会把最后一条当宿主"
    assert forged[-1]["content"] == "（插件伪造的用户行）"
    assert _enforce_user_message_last([], user_index=None) == 0
    assert _enforce_user_message_last([{"role": "user", "content": "甲"}], user_index=99) == 0
