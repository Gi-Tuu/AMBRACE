"""Read-only business/display projections; raw memory and reminder history stay intact.

Condition receipts are caches of persisted ChatMessage identity, never business
completion evidence. A cached receipt must pass the same source verification again.
"""
from datetime import timedelta
import hashlib
import json

from sqlalchemy import select,or_,and_
from sqlalchemy.exc import SQLAlchemyError
from app.models import ChatMessage,Memory,PlanItem,ProspectiveIntent
from app.memory import plan_completion as kernel
from app.utils.logger import get_logger

_logger = get_logger("memory.plan_projection")
from app.memory.plan_semantics import source_plan_specs,condition_signals,clauses,UNSAFE

KEYS = "business_item_keys"
RECEIPTS = "condition_receipts_v1"


def _meta(row):
    try:
        value = json.loads(row.cue_terms_json or "{}")
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):
        return {}


def _scope(row):
    try:
        return kernel.PlanScope(row.user_id,row.character_id,row.chat_session_id)
    except kernel.EvidenceConflict:
        return None


async def _item_spec(db,item):
    if item.cancelled or not await kernel.source_is_valid(db,item):
        return None
    source = await db.get(ChatMessage,item.source_message_id)
    return next((spec for spec in source_plan_specs(source.content)
                 if spec["ordinal"] == item.ordinal and spec["target"] == item.target_text),None)


def _fresh(event_at,due_end):
    if due_end is None:
        return True
    local = event_at + timedelta(hours=8)
    if (due_end.hour,due_end.minute) == (23,59):
        return local.date() >= due_end.date()
    return local >= due_end-timedelta(days=1)


async def _verified_receipt(db,item,condition,cached,due_end):
    if not condition.get("target") or not cached:
        return None
    try:
        source = await kernel._source(db,_scope(item),cached["source_id"])
        digest = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
        valid = (source.created_at.isoformat() == cached["event_at"] and digest == cached["source_hash"]
            and (source.created_at,source.id) > (item.source_event_at,item.source_message_id)
            and (condition["kind"],condition["target"]) in condition_signals(source.content)
            and _fresh(source.created_at,due_end))
        return cached if valid else None
    except (kernel.EvidenceConflict,KeyError,TypeError,ValueError):
        return None


async def _find_receipt(db,item,condition,due_end):
    if not condition.get("target"):
        return None  # An unspecified place is not an exact event target.
    event_at,message_id = item.source_event_at,item.source_message_id
    while True:
        rows = (await db.execute(select(ChatMessage).where(
            ChatMessage.session_id == item.chat_session_id,ChatMessage.sender_type == "user",
            or_(ChatMessage.created_at > event_at,
                and_(ChatMessage.created_at == event_at,ChatMessage.id > message_id))
        ).order_by(ChatMessage.created_at,ChatMessage.id).limit(128))).scalars().all()
        for source in rows:
            if ((condition["kind"],condition["target"]) in condition_signals(source.content)
                    and _fresh(source.created_at,due_end)):
                return {"source_id":source.id,"event_at":source.created_at.isoformat(),
                    "source_hash":hashlib.sha256(source.content.encode("utf-8")).hexdigest()}
        if len(rows) < 128:
            return None
        event_at,message_id = rows[-1].created_at,rows[-1].id


async def _states(db,items,receipts,due_end,*,find_condition=True):
    result = []
    for item in items:
        spec = await _item_spec(db,item)
        if spec is None:
            return None
        condition = spec["condition"]
        receipt = None
        if condition:
            receipt = await _verified_receipt(db,item,condition,receipts.get(item.item_key),due_end)
            if receipt is None and not receipts.get(item.item_key) and find_condition:
                receipt = await _find_receipt(db,item,condition,due_end)
        completed = await kernel.item_is_completed(db,item)
        result.append({"item":item,"spec":spec,"completed":completed,
            "condition_met":receipt is not None,"receipt":receipt,
            "reminder_met":completed or receipt is not None})
    return result


async def reminder_projection(db,row,*,persist=False):
    scope = _scope(row)
    if scope is None or row.source_message_id is None:
        return None
    try:
        source = await kernel._source(db,scope,row.source_message_id)
    except kernel.EvidenceConflict:
        return None
    meta = _meta(row)
    keys = meta.get(KEYS,[])
    if KEYS in meta and (not isinstance(keys,list) or not keys or
            any(not isinstance(key,str) or len(key) != 64 or
                any(ch not in "0123456789abcdef" for ch in key) for key in keys)):
        return {"reminder_done":False,"remaining":row.content,"states":[]}
    if RECEIPTS in meta and not isinstance(meta[RECEIPTS],dict):
        return {"reminder_done":False,"remaining":row.content,"states":[]}
    if not keys:
        if UNSAFE.search(source.content) or len(clauses(row.content)) > 1:
            return {"reminder_done":False,"remaining":row.content,"states":[]}
        return None
    items = (await db.execute(select(PlanItem).where(PlanItem.item_key.in_(keys)))).scalars().all()
    if len(items) != len(set(keys)) or any(_scope(item) != scope for item in items):
        return {"reminder_done":False,"remaining":row.content,"states":[]}
    states = await _states(db,items,meta.get(RECEIPTS,{}) if isinstance(meta.get(RECEIPTS),dict) else {},row.due_end)
    if states is None:
        return {"reminder_done":False,"remaining":row.content,"states":[]}
    covered = source_plan_specs(row.content)
    coverage = (len(covered) == len(clauses(row.content)) and
        all(any(state["item"].target_text == spec["target"] for state in states) for spec in covered))
    if persist:
        cached = meta.get(RECEIPTS,{})
        cached = cached if isinstance(cached,dict) else {}
        # Keep invalid receipts as tombstones: source edits cannot silently re-authorize themselves.
        meta[RECEIPTS] = {state["item"].item_key:state["receipt"] or cached.get(state["item"].item_key)
                          for state in states if state["receipt"] or cached.get(state["item"].item_key)}
        row.cue_terms_json = json.dumps(meta,ensure_ascii=False)
        await db.flush()
    remaining = "；".join(state["spec"]["clause"] for state in states if not state["reminder_met"])
    return {"reminder_done":bool(states) and coverage and all(s["reminder_met"] for s in states),
        "remaining":remaining if coverage else row.content,"states":states}


async def refresh_condition_receipts(db,row):
    await reminder_projection(db,row,persist=True)


async def _persisted_receipts(db,items):
    if not items:
        return {}
    scope = _scope(items[0])
    if scope is None:
        return {}
    rows = (await db.execute(select(ProspectiveIntent).where(
        ProspectiveIntent.user_id == scope.user_id,ProspectiveIntent.character_id == scope.character_id,
        ProspectiveIntent.chat_session_id == scope.chat_session_id,
        or_(*(ProspectiveIntent.cue_terms_json.like("%"+item.item_key+"%") for item in items))
    ))).scalars().all()
    receipts = {}
    for row in rows:
        meta = _meta(row)
        cached = meta.get(RECEIPTS,{})
        keys = meta.get(KEYS,[])
        if not isinstance(cached,dict) or not isinstance(keys,list):
            continue
        for item in items:
            if item.item_key not in keys or not cached.get(item.item_key):
                continue
            new = cached[item.item_key]
            old = receipts.get(item.item_key)
            receipts[item.item_key] = {"source_id":None} if old and old != new else new
    return receipts


def _business_note(states):
    completed = [s["item"].target_text for s in states if s["completed"]]
    open_tasks = [s["item"].target_text for s in states if not s["completed"]]
    condition_met = any(s["condition_met"] and not s["completed"] for s in states)
    pieces = []
    if completed:
        pieces.append("已有完成证据："+"、".join(completed))
    if condition_met:
        pieces.append("该事项的前置条件已有满足证据，后续工作不因此算完成")
    if open_tasks:
        pieces.append("尚无完成证据："+"、".join(open_tasks))
    return "[事项状态核验："+"；".join(pieces)+"]" if pieces else ""


async def project_memory_results(rows,character_id,factory):
    ids = [row.get("id") for row in rows if type(row.get("id")) is int and row["id"] > 0]
    if not ids:
        return rows
    try:
        async with factory() as db:
            memories = (await db.execute(select(Memory).where(
                Memory.id.in_(ids),Memory.character_id == character_id))).scalars().all()
            by_id = {m.id:m for m in memories}
            projected = []
            for row in rows:
                memory = by_id.get(row.get("id"))
                if (memory is None or not memory.source_id or memory.source != "chat"
                        or memory.group_id is not None or memory.speaker_type not in (None,"user")
                        or row.get("content") != memory.content):
                    projected.append(row)
                    continue
                items = (await db.execute(select(PlanItem).where(
                    PlanItem.user_id == memory.user_id,PlanItem.character_id == character_id,
                    PlanItem.source_message_id == memory.source_id))).scalars().all()
                items = [item for item in items if item.target_text in memory.content]
                receipts = await _persisted_receipts(db,items)
                states = await _states(db,items,receipts,None,find_condition=False) if items else None
                note = _business_note(states) if states else ""
                projected.append({**row,"content":note+"\n"+memory.content} if note else row)
            return projected
    except SQLAlchemyError as error:
        _logger.warning("Plan read projection unavailable (%s)",type(error).__name__)
        # No verified status is invented when a legacy/unavailable schema cannot be read.
        return rows


async def project_live_memory_results(rows,character_id):
    # Resolve the established service seam at call time, never cache its factory.
    from app.memory.service import async_session_factory
    return await project_memory_results(rows,character_id,async_session_factory)


async def projected_object_texts(objects,character_id,factory):
    rows = [{"id":m.id,"content":m.content} for m in objects if type(getattr(m,"id",None)) is int]
    view = await project_memory_results(rows,character_id,factory)
    return {row["id"]:row["content"] for row in view}
