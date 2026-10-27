"""Conservative literal adapters for the C9 ledger; no LLM or implicit commits.

This phase records generic completion assertions. Event preconditions and display
projections are separate. Unknown grammar stays unbound, never fuzzy-matched.
"""
import json
import re

from sqlalchemy import select, or_, and_
from app.models import ChatMessage, PlanItem, PlanCompletionEvidence
from app.memory import plan_completion as kernel

# Shared whole-message context gate: punctuation must not erase reported/uncertain speech.
from app.memory.plan_semantics import UNSAFE as _UNSAFE, NEGATIVE as _NEGATIVE, SPLIT as _SPLIT
_DONE = re.compile(r"^(?:(?:我)(?:已经|已)?(?:完成了|做完了)(?P<first>.+)|(?P<last>.+?)(?:已经|已)(?:完成|做完)(?:了)?)$")
_OTHER = re.compile(r"^(?:他|她|它|TA|ta|别人|同事|朋友|家人)")
_VAGUE = re.compile(r"^(?:这|那|它|全部|所有|事情|任务|工作|一切|这个|那个|这件事|那件事)$")
ITEM_KEYS = "business_item_keys"


def _target(value):
    value = (value or "").strip()
    if len(value) < 4 or _OTHER.match(value) or _VAGUE.fullmatch(value):
        return None
    return value


def plan_targets(content):
    from app.memory.plan_semantics import source_plan_specs
    return [(spec["ordinal"],spec["target"]) for spec in source_plan_specs(content)]


def completion_assertions(content):
    if _UNSAFE.search(content or ""):
        return []
    result = []
    for ordinal, clause in enumerate(_SPLIT.split(content or "")):
        clause = clause.strip()
        if _NEGATIVE.search(clause) or _OTHER.match(clause):
            continue
        match = _DONE.fullmatch(clause)
        target = _target((match.group("first") or match.group("last"))) if match else None
        if target:
            result.append((ordinal,target,clause))
    return result


def scope_for(row):
    try:
        return kernel.PlanScope(row.user_id,row.character_id,row.chat_session_id)
    except kernel.EvidenceConflict:
        return None


async def _verified_source(db, scope, source_id):
    if scope is None or source_id is None:
        return None
    try:
        return await kernel._source(db, scope, source_id)
    except kernel.EvidenceConflict:
        return None


async def reconcile_scope(db, scope):
    """Bind only an unambiguous complete literal target in this actual actor scope."""
    conditions = lambda cls: (cls.user_id == scope.user_id,
        cls.character_id == scope.character_id, cls.chat_session_id == scope.chat_session_id)
    items = (await db.execute(select(PlanItem).where(
        *conditions(PlanItem),PlanItem.cancelled.is_(False)))).scalars().all()
    evidence = (await db.execute(select(PlanCompletionEvidence).where(
        *conditions(PlanCompletionEvidence),or_(PlanCompletionEvidence.item_id.is_(None),
            PlanCompletionEvidence.binding_kind == "literal")))).scalars().all()
    valid = [item for item in items if await kernel.source_is_valid(db,item)]
    for ev in evidence:
        if ev.binding_kind == "ambiguous" or not await kernel.source_is_valid(db,ev):
            continue
        eligible = [item for item in valid if item.target_text == ev.target_text and
            (item.source_event_at,item.source_message_id) < (ev.source_event_at,ev.source_message_id)]
        if len(eligible) > 1 or (ev.item_id is not None and
                (not eligible or eligible[0].id != ev.item_id)):
            ev.item_id = None
            ev.binding_kind = "ambiguous"  # Later extraction must not preserve a premature unique match.
        elif len(eligible) == 1 and await kernel.bind_explicit(db,ev,eligible[0]):
            ev.binding_kind = "literal"
    await db.flush()


async def _record_source(db, scope, source):
    for ordinal,target,claim in completion_assertions(source.content):
        await kernel.record_assertion(db,scope,source.id,target,claim,ordinal=ordinal)


async def _replay_source_window(db, scope, source):
    """Recover missed post-commit hooks from actual history, without a last-N cutoff."""
    event_at, message_id = source.created_at, source.id
    while True:
        rows = (await db.execute(select(ChatMessage).where(
            ChatMessage.session_id == scope.chat_session_id,
            ChatMessage.sender_type == "user",
            or_(ChatMessage.created_at > event_at,
                and_(ChatMessage.created_at == event_at,ChatMessage.id > message_id))
        ).order_by(ChatMessage.created_at,ChatMessage.id).limit(128))).scalars().all()
        for message in rows:
            await _record_source(db,scope,message)
        if len(rows) < 128:
            return
        event_at, message_id = rows[-1].created_at, rows[-1].id


async def record_message(db, scope, source_id, text):
    """Legacy callers must resolve an actual matching user message too."""
    if not completion_assertions(text):
        return  # Ordinary chat does not rescan a ledger whose plans/evidence have not changed.
    if source_id is None:
        rows = (await db.execute(select(ChatMessage).where(
            ChatMessage.session_id == scope.chat_session_id,
            ChatMessage.sender_type == "user").order_by(
                ChatMessage.created_at.desc(),ChatMessage.id.desc()))).scalars().all()
        matched = next((row for row in rows if row.content.strip() == text.strip()),None)
        source_id = matched.id if matched is not None else None
    source = await _verified_source(db,scope,source_id)
    if source is None or source.content.strip() != text.strip():
        return
    await _record_source(db,scope,source)
    await reconcile_scope(db,scope)


async def register_for_intent(db, row, *, content, source_id, scope, side):
    """Attach stable item identities even if the reminder was display-merged."""
    if side != "user" or row.kind != "promise" or scope_for(row) != scope:
        return
    source = await _verified_source(db,scope,source_id)
    if source is None:
        return
    try:
        meta = json.loads(row.cue_terms_json or "{}")
    except (ValueError,TypeError):
        meta = {}
    if isinstance(meta,list):
        meta = {"terms":meta}
    if not isinstance(meta,dict):
        meta = {}
    keys = meta.get(ITEM_KEYS,[])
    keys = list(keys) if isinstance(keys,list) else []
    incoming = {target for _,target in plan_targets(content)}
    # Both the obligation and its ordinal come from the real source, not model output order.
    for ordinal,target in plan_targets(source.content):
        if target not in incoming:
            continue
        item = await kernel.register_item(db,scope,source_id,target,ordinal=ordinal)
        if item.item_key not in keys:
            keys.append(item.item_key)
    if keys:
        meta[ITEM_KEYS] = keys
        row.cue_terms_json = json.dumps(meta,ensure_ascii=False)
        await _replay_source_window(db,scope,source)
        from app.memory.plan_projection import refresh_condition_receipts
        await refresh_condition_receipts(db,row)
    await reconcile_scope(db,scope)
    await db.flush()
