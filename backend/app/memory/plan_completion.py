"""C9 business evidence kernel. Caller owns the transaction; no reminder writes.

Only structured assertions from a trusted parser enter this module. Source integrity
is checked here; semantic assertion classification belongs to the message adapter.
All event times are obtained from persisted user messages, never extraction time.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.tenant_service import same_tenant
from app.models import AICharacter, ChatMessage, ChatSession, PlanItem, PlanCompletionEvidence


class EvidenceConflict(ValueError):
    """Invalid source or inconsistent replay; caller must roll back the batch."""


@dataclass(frozen=True)
class PlanScope:
    user_id: int
    character_id: int
    chat_session_id: int

    def __post_init__(self):
        for value in (self.user_id, self.character_id, self.chat_session_id):
            if type(value) is not int or value <= 0:
                raise EvidenceConflict("Scope IDs must be positive integers")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key(kind, scope, message, ordinal):
    return _hash(json.dumps([kind, scope.user_id, scope.character_id, scope.chat_session_id,
                            message.id, _utc(message.created_at).isoformat(), ordinal],
                           separators=(",", ":")))


async def _source(db: AsyncSession, scope: PlanScope, source_id: int) -> ChatMessage:
    if type(source_id) is not int or source_id <= 0:
        raise EvidenceConflict("Source ID must be a positive integer")
    message = await db.get(ChatMessage, source_id, populate_existing=True)
    session = await db.get(ChatSession, scope.chat_session_id, populate_existing=True)
    character = await db.get(AICharacter, scope.character_id, populate_existing=True)
    if (not message or not session or not character or message.sender_type != "user"
            or message.session_id != scope.chat_session_id or session.user_id != scope.user_id
            or session.character_id != scope.character_id or not message.created_at
            or not await same_tenant(db, scope.user_id, character.user_id)):
        raise EvidenceConflict("Source does not belong to this user/character/session")
    return message


def _scope(row):
    return PlanScope(row.user_id, row.character_id, row.chat_session_id)


async def source_is_valid(db: AsyncSession, row) -> bool:
    if row.source_message_id is None:
        return False
    try:
        message = await _source(db, _scope(row), row.source_message_id)
    except EvidenceConflict:
        return False
    return (_utc(message.created_at) == row.source_event_at
            and _hash(message.content) == row.source_hash)


async def _persist(db, model, key_column, values):
    # Only the declared idempotency constraint is ignored. FK/other failures propagate.
    await db.execute(insert(model).values(**values).on_conflict_do_nothing(
        index_elements=[key_column]))
    row = (await db.execute(select(model).where(
        getattr(model, key_column) == values[key_column]).execution_options(
            populate_existing=True))).scalar_one()
    for name, value in values.items():
        if getattr(row, name) != value:
            raise EvidenceConflict("Replay changes the original assertion or plan")
    return row


def _ordinal(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceConflict("Ordinal must be a non-negative integer")


async def register_item(db: AsyncSession, scope: PlanScope, source_id: int,
                        target_text: str, *, ordinal: int = 0) -> PlanItem:
    _ordinal(ordinal)
    message = await _source(db, scope, source_id)
    if not target_text.strip() or target_text != target_text.strip() or target_text not in message.content:
        raise EvidenceConflict("Plan target must be a nonempty literal source span")
    return await _persist(db, PlanItem, "item_key", dict(
        item_key=_key("plan", scope, message, ordinal), user_id=scope.user_id,
        character_id=scope.character_id, chat_session_id=scope.chat_session_id,
        source_message_id=source_id, source_event_at=_utc(message.created_at),
        source_hash=_hash(message.content), ordinal=ordinal, target_text=target_text))


async def record_assertion(db: AsyncSession, scope: PlanScope, source_id: int,
                           target_text: str, claim_text: str, *, ordinal: int = 0
                           ) -> PlanCompletionEvidence:
    """Persist a parser-approved assertion; unresolved is an intentional durable state."""
    _ordinal(ordinal)
    message = await _source(db, scope, source_id)
    if (not target_text.strip() or target_text != target_text.strip() or not claim_text.strip()
            or claim_text not in message.content or target_text not in claim_text):
        raise EvidenceConflict("Assertion and target must be literal source spans")
    return await _persist(db, PlanCompletionEvidence, "evidence_key", dict(
        evidence_key=_key("completion", scope, message, ordinal), user_id=scope.user_id,
        character_id=scope.character_id, chat_session_id=scope.chat_session_id,
        source_message_id=source_id, source_event_at=_utc(message.created_at),
        source_hash=_hash(message.content), ordinal=ordinal,
        target_text=target_text, claim_text=claim_text))


async def bind_explicit(db: AsyncSession, evidence: PlanCompletionEvidence,
                        item: PlanItem) -> bool:
    """Only same-scope, source-backed, chronologically later evidence may bind."""
    # Resolve persisted rows afresh: detached or caller-mutated objects are not authority.
    evidence = await db.get(PlanCompletionEvidence, evidence.id, populate_existing=True)
    item = await db.get(PlanItem, item.id, populate_existing=True)
    if (not evidence or not item or _scope(evidence) != _scope(item) or item.cancelled
            or evidence.target_text != item.target_text
            or not await source_is_valid(db, evidence) or not await source_is_valid(db, item)
            or (evidence.source_event_at, evidence.source_message_id)
               <= (item.source_event_at, item.source_message_id)):
        return False
    if evidence.item_id is not None and evidence.item_id != item.id:
        raise EvidenceConflict("An assertion cannot be rebound to another obligation")
    evidence.item_id = item.id
    evidence.binding_kind = "explicit"
    await db.flush()
    return True


async def item_is_completed(db: AsyncSession, item: PlanItem) -> bool:
    item = await db.get(PlanItem, item.id, populate_existing=True)
    if not item or item.cancelled or not await source_is_valid(db, item):
        return False
    evidence_rows = (await db.execute(select(PlanCompletionEvidence).where(
        PlanCompletionEvidence.item_id == item.id))).scalars().all()
    for evidence in evidence_rows:
        if (_scope(evidence) == _scope(item) and evidence.target_text == item.target_text
                and await source_is_valid(db, evidence)
                and (evidence.source_event_at, evidence.source_message_id)
                    > (item.source_event_at, item.source_message_id)):
            return True
    return False
