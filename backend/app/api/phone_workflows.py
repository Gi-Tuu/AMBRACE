# 手机操作工作流 API（2026-08-14 P1+C）：用户自建工作流 CRUD + 步骤/画布校验
# - 旧格式：steps 线性数组（无分支）
# - 新格式（方案 C）：graph = {"nodes": [{id,action,...}], "edges": [{from,to,type,target?}]}
# - 两者互斥：有 graph 时 steps 存空数组；读取时返回 steps + graph
import json

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import get_db
from app.models.user_workflow import UserWorkflow
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/phone/workflows", tags=["Phone Workflows"])
_logger = get_logger("api.phone_workflows")

_ALLOWED_ACTIONS = {
    "click", "long_click", "scroll", "set_text",
    "launch_app", "tap_xy", "swipe", "back", "wait", "go_home",
}
_ALLOWED_EDGE_TYPES = {"success", "fail", "always", "screen_has", "screen_empty"}
_MAX_NODES = 20


def _validate_step(s: dict, i: int, with_id: bool = False, lang: str = "zh") -> dict:
    # 校验单个步骤/节点结构；返回规范化 dict（可含 id）
    if not isinstance(s, dict):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_step_bad_format", n=i + 1))
    action = str(s.get("action") or "").strip()
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_step_bad_action", n=i + 1, action=action))
    item: dict = {"action": action}
    if with_id:
        nid = str(s.get("id") or "").strip()
        if not nid:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_node_missing_id", n=i + 1))
        item["id"] = nid
    if action in ("click", "long_click", "scroll", "launch_app"):
        target = str(s.get("target") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_step_missing_target", n=i + 1))
        item["target"] = target[:50]
    if action == "set_text":
        text = str(s.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_step_missing_input", n=i + 1))
        item["text"] = text[:50]
    if action == "tap_xy":
        item["x"] = int(s.get("x") or 0)
        item["y"] = int(s.get("y") or 0)
    if action == "swipe":
        item["x1"] = int(s.get("x1") or 0)
        item["y1"] = int(s.get("y1") or 0)
        item["x2"] = int(s.get("x2") or 0)
        item["y2"] = int(s.get("y2") or 0)
        item["ms"] = int(s.get("ms") or 300)
    if action == "wait":
        item["ms"] = min(10000, max(100, int(s.get("ms") or 800)))
    item["confirm"] = bool(s.get("confirm"))
    return item


def _validate_steps(steps, lang: str = "zh") -> list[dict]:
    # 旧格式：线性步骤数组
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_steps_empty"))
    if len(steps) > 10:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_max_steps"))
    return [_validate_step(s, i, lang=lang) for i, s in enumerate(steps)]


def _validate_graph(graph: dict, lang: str = "zh") -> tuple[list[dict], list[dict]]:
    # 新格式：nodes + edges 画布（分支/条件）
    nodes_raw = graph.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "canvas_nodes_empty"))
    if len(nodes_raw) > _MAX_NODES:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_max_nodes", max=_MAX_NODES))
    ids: set[str] = set()
    nodes: list[dict] = []
    for i, n in enumerate(nodes_raw):
        nid = str(n.get("id") or "").strip() if isinstance(n, dict) else ""
        if not nid:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_node_missing_id", n=i + 1))
        if nid in ids:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_node_dup_id", nid=nid))
        ids.add(nid)
        nodes.append(_validate_step(n, i, with_id=True, lang=lang))
    edges_raw = graph.get("edges") or []
    if not isinstance(edges_raw, list):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_invalid"))
    edges: list[dict] = []
    for i, e in enumerate(edges_raw):
        if not isinstance(e, dict):
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_bad_format", n=i + 1))
        frm = str(e.get("from") or "").strip()
        to = str(e.get("to") or "").strip()
        if frm not in ids or to not in ids:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_bad_endpoint", n=i + 1))
        if frm == to:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_self", n=i + 1))
        etype = str(e.get("type") or "success").strip()
        if etype not in _ALLOWED_EDGE_TYPES:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_bad_type", n=i + 1, etype=etype))
        item: dict = {"from": frm, "to": to, "type": etype}
        if etype in ("screen_has", "screen_empty"):
            target = str(e.get("target") or "").strip()
            if not target:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_edge_missing_cond", n=i + 1))
            item["target"] = target[:50]
        edges.append(item)
    return nodes, edges


def _parse_graph(w: UserWorkflow) -> dict | None:
    if not w.graph:
        return None
    try:
        g = json.loads(w.graph)
        return g if isinstance(g, dict) else None
    except Exception:
        return None


def _to_dict(w: UserWorkflow) -> dict:
    try:
        steps = json.loads(w.steps or "[]")
    except Exception:
        steps = []
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "steps": steps,
        "graph": _parse_graph(w),
        "enabled": w.enabled,
        "created_at": w.created_at.isoformat() if w.created_at else "",
    }


@router.get("")
async def list_workflows(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    rows = (
        await db.execute(
            select(UserWorkflow).where(UserWorkflow.user_id == user_id).order_by(UserWorkflow.id.desc())
        )
    ).scalars().all()
    return {"items": [_to_dict(w) for w in rows], "total": len(rows)}


@router.post("")
async def create_workflow(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    name = str(data.get("name") or "").strip()[:50]
    if not name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_name"))
    steps: list[dict] = []
    graph: dict | None = None
    if "steps" in data and data["steps"] is not None:
        steps = _validate_steps(data["steps"], lang)
    elif isinstance(data.get("graph"), dict):
        nodes, edges = _validate_graph(data["graph"], lang)
        graph = {"nodes": nodes, "edges": edges}
    else:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "wf_missing_step_canvas"))
    desc = str(data.get("description") or "").strip()[:200] or None
    w = UserWorkflow(
        user_id=user_id, name=name, description=desc,
        steps=json.dumps(steps, ensure_ascii=False),
        graph=json.dumps(graph, ensure_ascii=False) if graph else None,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    _logger.info("Workflow created id=%d name=%s", w.id, name)
    return _to_dict(w)


@router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    w = (
        await db.execute(
            select(UserWorkflow).where(
                UserWorkflow.id == workflow_id, UserWorkflow.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "workflow_not_found"))
    if "name" in data:
        w.name = str(data["name"] or "").strip()[:50] or w.name
    if "description" in data:
        w.description = str(data["description"] or "").strip()[:200] or None
    if "steps" in data and data["steps"] is not None:
        w.steps = json.dumps(_validate_steps(data["steps"], lang), ensure_ascii=False)
        w.graph = None
    if "graph" in data:
        if data["graph"] is None:
            w.graph = None
        elif isinstance(data["graph"], dict):
            nodes, edges = _validate_graph(data["graph"], lang)
            w.steps = "[]"
            w.graph = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
        else:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "canvas_invalid"))
    if "enabled" in data:
        w.enabled = bool(data["enabled"])
    await db.commit()
    await db.refresh(w)
    return _to_dict(w)


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    w = (
        await db.execute(
            select(UserWorkflow).where(
                UserWorkflow.id == workflow_id, UserWorkflow.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "workflow_not_found"))
    await db.execute(delete(UserWorkflow).where(UserWorkflow.id == workflow_id))
    await db.commit()
    return {"status": "ok"}
