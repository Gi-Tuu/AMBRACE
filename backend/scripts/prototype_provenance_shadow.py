"""G3/G4 离线契约原型 v2（纯标准库 + 本机 jsonschema；全内存；不 import app/ORM/网络）。

只证明「可信归属 + 版本化来源 + 撤销墓碑 + 清除 + UTC 事件序 + 投影」这组契约自洽；
不代表产品实现/真实迁移/真实事务/真实数据/模型质量。用例全部 synthetic。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import jsonschema

ENVELOPE_VERSION = 1
KNOWN_ACTIONS = frozenset({
    "create", "update", "merge", "supersede", "stale", "reject", "downgrade",
})
SOURCE_KINDS = frozenset({
    "chat_message", "diary_entry", "moment", "phone_snapshot", "memory_derivation", "unknown",
})
ACTOR_KINDS = frozenset({"authenticated_user", "system_task", "model", "unknown"})
EXECUTOR_KINDS = frozenset({"service", "model", "scheduler"})
SUBJECT_KINDS = frozenset({"user", "character", "system", "unknown"})
TRUSTED_SYSTEM_TASKS = frozenset({
    "cascade_stale", "supersede", "account_purge", "memory_maintenance", "scheduler",
})
REASON_CODES = frozenset({
    "user_corrected", "user_merged", "user_retracted", "auto_superseded", "cascade_stale",
    "reflection_downgrade", "admission_pending", "core_promote", "merge_incoming",
    "source_deleted", "unknown",
})
# 列 ↔ 信封映射冻结（R5）：列值只由可信写时上下文/服务端规则产生，wire 只能提供声明，不能自证。
ACTOR_KIND_TO_HISTORICAL = {
    "authenticated_user": "user",
    "system_task": "system",
    "model": "model",
    "unknown": "unknown",
}
MAX_EXCERPT_CHARS = 120
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_SCHEMA = None


class ContractError(ValueError):
    """坏信封/坏投影/违反契约；读侧应降 unknown，不得伪装已知。"""


class UnsupportedVersion(ContractError):
    """未知信封版本：保守拒读。"""


class NotVisible(ContractError):
    """跨租户/跨角色/无主/主体已删：不泄露存在性。"""


class Gone(ContractError):
    """用户明确遗忘或撤回：正常解释卡不展示。"""


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_utc(value: str, field: str, *, allow_naive_as_utc: bool = True) -> datetime:
    """ISO→UTC 瞬间；`Z`/offset 支持；naive 按项目口径视为 UTC（文档明示，不暗中当本地时区）。"""
    if not isinstance(value, str) or not _ISO_RE.match(value):
        raise ContractError(f"{field} must be ISO-8601")
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractError(f"{field} invalid ISO: {exc}") from None
    if parsed.tzinfo is None:
        if not allow_naive_as_utc:
            raise ContractError(f"{field} requires timezone")
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_envelope(obj: dict) -> list[str]:
    """手动语义校验（Schema 校验之外的跨字段规则）。返回错误列表。"""
    errors: list[str] = []
    version = obj.get("v")
    if version != ENVELOPE_VERSION:
        raise UnsupportedVersion(f"unsupported envelope version: {version!r}")
    if not isinstance(obj.get("record_id"), str) or not obj["record_id"]:
        errors.append("record_id required")
    if not _is_pos_int(obj.get("character_id")):
        errors.append("character_id must be positive int")
    memory_id = obj.get("memory_id")
    if memory_id is not None and not _is_pos_int(memory_id):
        errors.append("memory_id must be positive int or null")
    if obj.get("action") not in KNOWN_ACTIONS:
        errors.append("action unknown")
    if not isinstance(obj.get("idempotency_key"), str) or not obj["idempotency_key"]:
        errors.append("idempotency_key required")
    try:
        to_utc(obj.get("captured_at"), "captured_at")
        if obj.get("source_event_at") is not None:
            to_utc(obj["source_event_at"], "source_event_at")
    except ContractError as exc:
        errors.append(str(exc))

    source = obj.get("source")
    if not isinstance(source, dict):
        errors.append("source container required")
    else:
        kind = source.get("kind")
        if kind not in SOURCE_KINDS:
            errors.append("source.kind unknown")
        sid = source.get("id")
        generation = source.get("generation")
        if generation is not None and not _is_pos_int(generation):
            errors.append("source.generation must be positive int")
        if kind in ("chat_message", "diary_entry", "moment", "phone_snapshot"):
            if not _is_pos_int(sid):
                errors.append("source.id must be positive int for typed source")
            if not isinstance(source.get("version"), str) or not source["version"]:
                errors.append("source.version required for typed source")
            if not _is_pos_int(generation):
                errors.append("source.generation must be positive int for typed source")
        elif kind == "memory_derivation":
            if sid is not None and not _is_pos_int(sid):
                errors.append("source.id invalid for derivation")
        elif kind == "unknown" and sid is not None:
            errors.append("source.id must be null when kind=unknown")

    actor = obj.get("actor")
    if not isinstance(actor, dict):
        errors.append("actor container required")
    else:
        kind = actor.get("kind")
        if kind not in ACTOR_KINDS:
            errors.append("actor.kind unknown")
        elif kind == "authenticated_user":
            if not _is_pos_int(actor.get("user_id")):
                errors.append("actor.user_id required for authenticated_user")
            if actor.get("authenticated") is not True:
                errors.append("authenticated_user requires authenticated=true")
        elif kind == "system_task" and actor.get("task") not in TRUSTED_SYSTEM_TASKS:
            errors.append("system_task.task not in trusted allowlist")

    executor = obj.get("executor")
    if not isinstance(executor, dict):
        errors.append("executor container required")
    elif executor.get("kind") not in EXECUTOR_KINDS:
        errors.append("executor.kind unknown")

    subject = obj.get("content_subject")
    if not isinstance(subject, dict):
        errors.append("content_subject container required")
    else:
        kind = subject.get("kind")
        if kind not in SUBJECT_KINDS:
            errors.append("content_subject.kind unknown")
        elif kind in ("user", "character") and not _is_pos_int(subject.get("id")):
            errors.append("content_subject.id required for user/character")
        elif kind in ("system", "unknown") and subject.get("id") is not None:
            errors.append("content_subject.id must be null for system/unknown")

    reason = obj.get("reason")
    if not isinstance(reason, dict):
        errors.append("reason container required")
    else:
        if reason.get("code") not in REASON_CODES:
            errors.append("reason.code unknown")
        text = reason.get("text")
        if text is not None and not isinstance(text, str):
            errors.append("reason.text must be string or null")
        if text is not None and reason.get("text_visible") not in (True, False):
            errors.append("reason.text_visible required when text present")

    evidence = obj.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence container required")
    else:
        retention = evidence.get("retention")
        excerpt = evidence.get("excerpt")
        if retention not in ("none", "ephemeral_verified"):
            errors.append("evidence.retention unknown")
        if retention == "none" and excerpt is not None:
            errors.append("excerpt not allowed when retention=none")
        if excerpt is not None:
            if not isinstance(excerpt, str) or len(excerpt) > MAX_EXCERPT_CHARS:
                errors.append("excerpt too long or not string")
            if evidence.get("excerpt_sha256") != _sha(excerpt):
                errors.append("excerpt_sha256 mismatch")
            if not isinstance(evidence.get("source_version"), str):
                errors.append("evidence.source_version required with excerpt")

    derivation = obj.get("derivation")
    if not isinstance(derivation, dict):
        errors.append("derivation container required")
    else:
        parents = derivation.get("parent_ids")
        if not isinstance(parents, list):
            errors.append("derivation.parent_ids must be list")
        elif any(not _is_pos_int(p) for p in parents):
            errors.append("derivation.parent_ids must be positive ints")
        elif len(parents) != len(set(parents)):
            errors.append("derivation.parent_ids duplicated")
    return errors


def load_schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        path = __import__("pathlib").Path(__file__).parent / "provenance_shadow_contract.schema.json"
        _SCHEMA = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA


def schema_validate(instance: dict, kind: str) -> None:
    """真实 Draft7 校验（本机 jsonschema，无远程 $ref）；失败统一 ContractError。"""
    root = load_schema()
    wrapper = {"$schema": root.get("$schema", "http://json-schema.org/draft-07/schema#"),
               "definitions": root["definitions"],
               "allOf": [{"$ref": f"#/definitions/{kind}"}]}
    try:
        jsonschema.Draft7Validator(wrapper).validate(instance)
    except jsonschema.ValidationError as exc:
        raise ContractError(f"schema:{kind}:{exc.message}") from None


def parse_envelope(payload) -> dict:
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            raise ContractError("bad_json") from None
    if not isinstance(payload, dict):
        raise ContractError("envelope must be an object")
    if payload.get("v") != ENVELOPE_VERSION:
        raise UnsupportedVersion(f"unsupported envelope version: {payload.get('v')!r}")
    schema_validate(payload, "envelope")
    errors = validate_envelope(payload)
    if errors:
        raise ContractError("; ".join(errors))
    return json.loads(json.dumps(payload, ensure_ascii=False))


@dataclass(frozen=True)
class TrustedWriteContext:
    """服务端在写入时构造的可信上下文（不是 wire 字段，不来自客户端自报）。"""
    actor_kind: str
    resource_owner_user_id: int | None
    actor_user_id: int | None = None
    task: str | None = None
    request_authenticated: bool = False
    reallow_grant: str | None = None  # 仅服务端在可信确认后签发，wire 不可自带

    def __post_init__(self):
        if self.actor_kind not in ACTOR_KINDS:
            raise ContractError("trusted actor_kind unknown")
        if self.actor_kind == "authenticated_user":
            if not self.request_authenticated or not _is_pos_int(self.actor_user_id):
                raise ContractError("trusted authenticated_user requires authenticated request")
        if self.actor_kind == "system_task" and self.task not in TRUSTED_SYSTEM_TASKS:
            raise ContractError("trusted system task not in allowlist")


def build_actor_from_request(*, claimed_actor_kind: str | None,
                             request_authenticated_user_id: int | None) -> dict:
    """builder 输出必须通过 Schema；wire 自称被忽略，只信已认证请求者。"""
    if not _is_pos_int(request_authenticated_user_id):
        actor = {"kind": "unknown"}
    else:
        actor = {"kind": "authenticated_user", "user_id": request_authenticated_user_id,
                 "authenticated": True}
    schema_validate(actor, "actor")
    return actor


def order_receipts(receipts: list[dict]) -> list[dict]:
    """事件时间归一化 UTC 后排序；缺失事件时间排最后；同瞬间 record_id 稳定 tie-break。"""
    def key(item):
        event = item.get("source_event_at")
        if event is None:
            return (1, datetime.max.replace(tzinfo=timezone.utc), item["record_id"])
        return (0, to_utc(event, "source_event_at"), item["record_id"])
    return sorted(receipts, key=key)


_SENSITIVE_KEYS = ("excerpt", "text", "content_excerpt", "content_preview")


def _source_in_revoked(envelope: dict, tombstone: dict) -> bool:
    """R3：撤销时已存在的**任何**来源代际都算旧事件；不同 source.id 不证明新鲜度。"""
    source = envelope["source"]
    revoked = tombstone.get("revoked_sources") or []
    return [source["kind"], source["id"], source.get("generation")] in revoked


def _scrub_obj(obj, *, source_key=None, memory_id=None):
    """递归清除片段/原因文本/嵌套副本；quarantine 同样按本函数清理。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _SENSITIVE_KEYS and isinstance(v, str):
                out[k] = None
            else:
                out[k] = _scrub_obj(v, source_key=source_key, memory_id=memory_id)
        if "evidence" in out and isinstance(out["evidence"], dict):
            out["evidence"]["retention"] = "none"
            out["evidence"]["excerpt_sha256"] = None
            out["evidence"]["source_version"] = None
        return out
    if isinstance(obj, list):
        return [_scrub_obj(v, source_key=source_key, memory_id=memory_id) for v in obj]
    return obj


class ProvenanceStore:
    """最小状态机：可信归属、源代际、墓碑、清除、事件序投影。"""

    def __init__(self):
        self.sources: dict[tuple[str, int], dict] = {}
        self.memories: dict[int, dict] = {}
        self.receipts: dict[int, list[dict]] = {}
        self.tombstones: dict[int, dict] = {}
        self.grants: dict[str, dict] = {}
        self._source_seq = 0
        self._revocation_round = 0
        self._idem: set[str] = set()
        self._late: list[dict] = []

    # ── 源 ──
    def issue_source_proof(self) -> int:
        """由来源服务（可信组件）签发的单调事件序号；登记顺序本身不作证明。"""
        self._source_seq += 1
        return self._source_seq

    def add_source(self, kind: str, source_id: int, version: str, content: str,
                   *, trusted_seq: int | None = None) -> None:
        """trusted_seq 必须来自来源服务签发的原始事件序号；缺省=未证明（不可作新事件资格）。"""
        prev = self.sources.get((kind, source_id))
        generation = 1 if prev is None else int(prev["generation"]) + 1
        if trusted_seq is not None:
            if not _is_pos_int(trusted_seq):
                raise ContractError("trusted_seq must be positive int")
            self._source_seq = max(self._source_seq, int(trusted_seq))
        self.sources[(kind, source_id)] = {
            "version": version, "content": content, "deleted": False,
            "withdrawn": False, "generation": generation,
            "proof_seq": trusted_seq,
        }

    def edit_source(self, kind: str, source_id: int, version: str, content: str) -> None:
        item = self.sources.get((kind, source_id))
        if item is None or item["deleted"]:
            raise ContractError("source missing or deleted")
        item["version"], item["content"] = version, content

    def delete_source(self, kind: str, source_id: int, *, withdrawn: bool = False) -> None:
        item = self.sources.get((kind, source_id))
        if item is not None:
            item["deleted"] = True
            item["withdrawn"] = bool(withdrawn)
            item["content"] = ""
            item["generation"] = int(item["generation"]) + 1
        # R3：清除所有持久副本（receipts/晚到隔离/嵌套 incoming），不仅 UI 隐藏
        self._scrub_matching(source_kind=kind, source_id=source_id)

    def _scrub_matching(self, *, source_kind: str, source_id: int) -> None:
        for memory_id, items in self.receipts.items():
            for index, envelope in enumerate(items):
                src = envelope.get("source") or {}
                if src.get("kind") == source_kind and src.get("id") == source_id:
                    items[index] = _scrub_obj(envelope)
        self._late = [
            _scrub_obj(env) if (env.get("source") or {}).get("kind") == source_kind and
            (env.get("source") or {}).get("id") == source_id else env
            for env in self._late
        ]

    # ── 写入 ──
    def ingest(self, payload, *, trusted: TrustedWriteContext | None = None) -> str:
        envelope = parse_envelope(payload)
        if trusted is None:
            # R1：无可信写时上下文 ⇒ 不接受 wire 自报 actor/subject 生成归属或可读资源。
            # 不持久化原文；只留脱敏最小拒绝记录（便于审计计数）。
            envelope["_owner_user_id"] = None
            envelope["_owner_basis"] = "untrusted_wire"
            envelope["_trusted_actor"] = {"kind": "unknown", "user_id": None, "task": None}
            self._late.append(_scrub_obj(envelope))
            return "refused_untrusted_wire"
        envelope["_trusted_actor"] = {
            "kind": trusted.actor_kind,
            "user_id": trusted.actor_user_id,
            "task": trusted.task,
        }
        envelope["_owner_user_id"] = trusted.resource_owner_user_id
        envelope["_owner_basis"] = "trusted_write_context"
        if trusted.resource_owner_user_id is None:
            return "refused_unowned"

        # R3：墓碑与重新允许资格（新 key/不同 source.id 都不算新事件）
        tomb = self.tombstones.get(envelope["memory_id"])
        if tomb is not None:
            if tomb.get("state") == "closed":
                return "refused_tombstoned"
            if _source_in_revoked(envelope, tomb):
                return "refused_stale_source"
            # 任何墓碑后的写入（含 active）都必须先过 owner/角色/资源作用域
            if trusted.resource_owner_user_id != tomb.get("owner_user_id"):
                return "refused_owner_scope"
            if envelope["character_id"] != tomb.get("character_id"):
                return "refused_character_scope"
            if trusted.actor_kind == "authenticated_user" and \
                    trusted.actor_user_id != tomb.get("owner_user_id"):
                return "refused_actor_scope"
            grant = self.grants.get(trusted.reallow_grant or "")
            if tomb.get("state") == "reallowed":
                if not grant or grant.get("used") or grant.get("invalidated") or \
                        grant.get("memory_id") != envelope["memory_id"] or \
                        grant.get("confirmation_epoch") != tomb.get("confirmation_epoch"):
                    return "refused_no_grant"
                if grant.get("owner_user_id") != tomb.get("owner_user_id") or \
                        grant.get("confirmer_user_id") != trusted.actor_user_id or \
                        grant.get("character_id") != envelope["character_id"] or \
                        grant.get("revocation_round") != tomb.get("round") or \
                        grant.get("confirm_seq") != tomb.get("confirm_seq") or \
                        trusted.resource_owner_user_id != tomb.get("owner_user_id"):
                    return "refused_grant_scope"
            elif tomb.get("state") != "reallowed_active":
                return "refused_tombstoned"
            elif trusted.reallow_grant:
                # active 普通更新不得复用/携带旧确认令牌
                return "refused_grant_reuse"
            # 来源新鲜度只能由来源服务原始事件序号证明；必须严格晚于确认边界
            source_proof = None
            if envelope["source"]["kind"] in ("chat_message", "diary_entry",
                                              "moment", "phone_snapshot"):
                item = self.sources.get((envelope["source"]["kind"],
                                         envelope["source"]["id"]))
                source_proof = None if item is None else item.get("proof_seq")
            if source_proof is None:
                return "refused_unproven_source"
            if int(source_proof) <= int(tomb.get("revoked_seq") or 0):
                return "refused_stale_source"
            if int(source_proof) <= int(tomb.get("confirm_seq") or 0):
                return "refused_not_after_confirmation"

        key = envelope["idempotency_key"]
        if key in self._idem:
            return "duplicate"
        self._idem.add(key)
        source = envelope["source"]
        if source["kind"] in ("chat_message", "diary_entry", "moment", "phone_snapshot"):
            item = self.sources.get((source["kind"], source["id"]))
            if item is None or item["deleted"]:
                envelope["_late_state"] = "quarantined_after_source_deleted"
                self._late.append(_scrub_obj(envelope))
                return "quarantined"
            # R2：事件必须携带**它自己**的 generation；与当前源不符即拒绝/隔离，
            # 绝不把到达时当前 generation 赋给旧事件冒充验证。
            if int(source.get("generation") or 0) != int(item["generation"]):
                envelope["_late_state"] = "quarantined_stale_generation"
                self._late.append(_scrub_obj(envelope))
                return "refused_stale_generation"
        memory_id = envelope["memory_id"]
        if memory_id is None:
            return "recorded_without_memory"
        self.receipts.setdefault(memory_id, []).append(envelope)
        if tomb is not None:
            if trusted.reallow_grant:
                grant = self.grants.get(trusted.reallow_grant)
                if grant is not None and not grant.get("used"):
                    grant["used"] = True
                    grant["consumed_by_memory"] = memory_id
            tomb["state"] = "reallowed_active"
            tomb["reborn_source"] = [source["kind"], source["id"],
                                     source.get("generation")]
        if envelope["action"] == "create":
            self.memories[memory_id] = {
                "memory_id": memory_id,
                "character_id": envelope["character_id"],
                "owner_user_id": envelope.get("_owner_user_id"),
                "owner_basis": envelope.get("_owner_basis"),
                "status": "active",
                "source_kind": source.get("kind"),
                "source_id": source.get("id"),
                "source_version": source.get("version"),
            }
        return "accepted"

    def late_after_delete(self, payload, *, trusted=None) -> str:
        return self.ingest(payload, trusted=trusted)

    def add_memory_without_receipt(self, memory_id: int, *, character_id: int,
                                  owner_user_id: int | None) -> None:
        self.memories[memory_id] = {
            "memory_id": memory_id, "character_id": character_id,
            "owner_user_id": owner_user_id, "owner_basis": "untrusted_unknown",
            "status": "active", "source_kind": "unknown", "source_id": None,
            "source_version": None,
        }

    # ── 撤销/遗忘/恢复 ──
    def _close_tombstone(self, memory_id: int, kind: str, reason_code: str) -> None:
        mem = self.memories.get(memory_id) or {}
        prev = self.tombstones.get(memory_id) or {}
        owner = mem.get("owner_user_id") or prev.get("owner_user_id")
        character = mem.get("character_id") or prev.get("character_id")
        items = [r for r in self.receipts.get(memory_id, []) if r.get("source_event_at")]
        latest = order_receipts(items)[-1] if items else None
        closed_source = None
        if latest is not None:
            src = latest["source"]
            closed_source = {"kind": src["kind"], "id": src["id"],
                             "generation": src.get("generation")}
        # 撤销时把**当前所有来源代际**冻结为撤销快照；之后引用任一旧代际都被视为旧事件。
        revoked_sources = [[k[0], k[1], int(v["generation"])]
                           for k, v in self.sources.items()]
        self.memories.pop(memory_id, None)
        self._revocation_round += 1
        for grant in self.grants.values():
            if grant.get("memory_id") == memory_id:
                grant["used"] = True
        self.tombstones[memory_id] = {
            "kind": kind, "reason_code": reason_code,
            "owner_user_id": owner,
            "character_id": character,
            "closed_source": closed_source, "revoked_sources": revoked_sources,
            "revoked_seq": self._source_seq,
            "round": self._revocation_round,
            "state": "closed",
        }
        for index, envelope in enumerate(self.receipts.get(memory_id, [])):
            self.receipts[memory_id][index] = _scrub_obj(envelope, memory_id=memory_id)
        self._late = [_scrub_obj(env) if env.get("memory_id") == memory_id else env
                      for env in self._late]

    def forget_memory(self, memory_id: int) -> None:
        self._close_tombstone(memory_id, "forgotten", "user_retracted")

    def retract_memory(self, memory_id: int, reason_code: str = "user_retracted") -> None:
        self._close_tombstone(memory_id, "retracted", reason_code)

    def reallow_new_memory(self, memory_id: int, *, user_confirmed: bool = False,
                           trusted: TrustedWriteContext | None = None) -> bool:
        """R3：确认身份必须来自可信上下文；boolean 只登记请求，不授权。

        - trusted 确认者必须是 tombstone.owner（认证用户）；
        - 授权返回服务端签发的单次 grant（truthy）；写入时必须由可信上下文携带；
        - 撤销快照冻结了确认时已存在的全部来源代际：不同 source.id 也是旧事件，一律拒绝；
        - 真正新事件须引用确认后新出现的源代际（或新源），且携带该 grant。
        """
        tomb = self.tombstones.get(memory_id)
        if tomb is None:
            return False
        if trusted is None:
            if user_confirmed:
                tomb["confirmation_requested"] = True  # 仅登记，不解除撤销边界
                return True
            return False
        if trusted.actor_kind != "authenticated_user" or not trusted.request_authenticated:
            return False
        owner = tomb.get("owner_user_id")
        if owner is None or trusted.resource_owner_user_id != owner or \
                trusted.actor_user_id != owner:
            return False
        # 重复确认必须换代：独立 epoch +1；上一确认未用 grant 立即失效
        epoch = int(tomb.get("confirmation_epoch") or 0) + 1
        tomb["state"] = "reallowed"
        tomb["reallowed_by"] = owner
        tomb["confirmation_epoch"] = epoch
        tomb["confirm_seq"] = self._source_seq
        for grant in self.grants.values():
            if grant.get("memory_id") == memory_id and not grant.get("used"):
                grant["invalidated"] = True
        token = "grant-" + uuid.uuid4().hex
        self.grants[token] = {
            "memory_id": memory_id, "used": False,
            "owner_user_id": owner, "confirmer_user_id": trusted.actor_user_id,
            "character_id": (self.memories.get(memory_id) or {}).get("character_id")
            or tomb.get("character_id") or 11,
            "revocation_round": tomb.get("round"),
            "confirm_seq": self._source_seq,
            "confirmation_epoch": epoch,
        }
        return token

    def restore_from_backup(self, memory_id: int, payload) -> str:
        """备份恢复：整库默认隔离不可见；旧证据不自我证明，需独立撤销资料重核。"""
        envelope = parse_envelope(payload)
        self.memories[memory_id] = {
            "memory_id": memory_id, "character_id": envelope["character_id"],
            "owner_user_id": None, "owner_basis": "restored_backup_unverified",
            "status": "restored_unverified", "source_kind": envelope["source"]["kind"],
            "source_id": envelope["source"]["id"],
            "source_version": envelope["source"]["version"],
        }
        self.receipts.setdefault(memory_id, []).append(_scrub_obj(envelope))
        return "restored_unverified"

    # ── 投影 ──
    def project(self, memory_id: int, *, viewer_user_id: int, scope_user_ids: set[int],
                viewer_character_id: int, mode: str = "family") -> dict:
        mem = self.memories.get(memory_id)
        if mem is None:
            raise Gone("memory gone or withdrawn")
        if mem["character_id"] != viewer_character_id:
            raise NotVisible("character mismatch")
        owner = mem.get("owner_user_id")
        # R1：family 模式无 owner 同样拒读；user 模式仅本人；legacy wire 归属不参与家庭共享
        if owner is None:
            raise NotVisible("resource owner unknown")
        if mode == "user":
            if owner != viewer_user_id:
                raise NotVisible("user-mode owner mismatch")
        else:
            if mem.get("owner_basis") == "legacy_envelope_self" and owner != viewer_user_id:
                raise NotVisible("legacy receipt is owner-only")
            if owner not in scope_user_ids:
                raise NotVisible("owner outside tenant scope")

        receipts = [r for r in self.receipts.get(memory_id, [])]
        if not receipts:
            return _projection(memory_id, "missing", {"kind": "unknown"}, "unknown",
                               None, "unverified", "unknown", "unknown")
        ordered = [r for r in order_receipts(receipts) if r.get("source_event_at") is not None]
        if not ordered:
            return _projection(memory_id, "present", {"kind": "unknown"}, "unknown",
                               None, "unverified", "unknown", "unknown")
        latest = ordered[-1]
        if latest.get("_owner_user_id") not in (None, owner):
            # 列与信封归属冲突：拒读（R1）
            raise NotVisible("owner conflict")
        actor = _trusted_actor_view(latest)
        evidence_state, excerpt = _evidence_view(latest, mem, self.sources)
        reason_code = latest["reason"]["code"]
        reason_text = latest["reason"].get("text")
        if not (latest["reason"].get("text_visible") is True and reason_text):
            reason_text = None
        if evidence_state in ("source_deleted", "source_changed", "unverified", "unavailable"):
            # R3：依赖源正文的原因文本在源不可信时一律不展示
            reason_text = None
            if reason_code in ("user_corrected", "user_merged", "merge_incoming"):
                reason_code = "unknown"
        derivation_state = "current" if latest["derivation"]["parent_ids"] else "none"
        return _projection(memory_id, "present", actor, reason_code, reason_text,
                           evidence_state, _source_label(latest), derivation_state, excerpt)


def _trusted_actor_view(envelope: dict) -> dict:
    """历史操作者来自写时可信上下文；legacy wire 一律 unknown，不与当前查看者混同。"""
    trusted = envelope.get("_trusted_actor") or {}
    kind = trusted.get("kind", "unknown")
    if kind == "authenticated_user":
        return {"kind": "user", "basis": "trusted_write_context"}
    if kind == "system_task":
        return {"kind": "system", "basis": "trusted_write_context"}
    if kind == "model":
        return {"kind": "model", "basis": "trusted_write_context"}
    return {"kind": "unknown", "basis": "legacy_or_untrusted"}


def _source_label(envelope: dict) -> str:
    return envelope["source"]["kind"]


def _evidence_view(envelope: dict, mem: dict, sources: dict) -> tuple[str, str | None]:
    source = envelope["source"]
    evidence = envelope["evidence"]
    if source["kind"] in ("unknown", "memory_derivation"):
        return "unverified", None
    item = sources.get((source["kind"], source["id"]))
    if item is None:
        return "unverified", None
    # 列 vs 信封指针冲突 ⇒ 拒读（不各自当真源）
    if (mem.get("source_kind"), mem.get("source_id")) not in ((None, None),
            (source["kind"], source["id"])):
        return "unverified", None
    if item["deleted"]:
        return "source_deleted", None
    claimed_generation = int(source.get("generation") or 0)
    if claimed_generation != int(item["generation"]):
        return "source_changed", None
    if item["version"] != source.get("version") or \
            evidence.get("source_version") != item["version"]:
        return "source_changed", None
    if evidence.get("retention") != "ephemeral_verified" or not evidence.get("excerpt"):
        return "none", None
    return "current", evidence["excerpt"]


def _projection(memory_id, receipt_state, actor, reason_code, reason_text, evidence_state,
                source_state, derivation_state, excerpt=None) -> dict:
    return {
        "memory_id": memory_id,
        "receipt_state": receipt_state,
        "actor": actor,
        "source": {"kind": source_state, "label": source_state},
        "evidence": {"state": evidence_state, "excerpt": excerpt},
        "reason": {
            "state": "known" if reason_code not in (None, "unknown") else "unknown",
            "code": reason_code if reason_code not in (None, "unknown") else "unknown",
            "text": reason_text,
        },
        "derivation": {"state": derivation_state},
        "as_of": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def g5_read_only_draft(projection: dict) -> dict:
    return {
        "memory_id": projection["memory_id"],
        "why": {
            "actor_kind": projection["actor"]["kind"],
            "reason_code": projection["reason"]["code"],
            "reason_state": projection["reason"]["state"],
        },
        "evidence": {
            "state": projection["evidence"]["state"],
            "excerpt": projection["evidence"]["excerpt"],
        },
        "source_kind": projection["source"]["kind"],
        "receipt_state": projection["receipt_state"],
        "derivation_state": projection["derivation"]["state"],
    }


def _ctx_from_spec(spec, store=None):
    data = dict(spec or {})
    grant_memory = data.pop("grant_for_memory", None)
    if grant_memory is not None and store is not None:
        for token, grant in store.grants.items():
            if grant.get("memory_id") == grant_memory and not grant.get("used"):
                data["reallow_grant"] = token
                break
    if data.pop("use_last_grant", False) and store is not None:
        data["reallow_grant"] = getattr(store, "_cli_last_grant", None)
    if data.get("reallow_grant") == "wire-forged":
        data["reallow_grant"] = None
    return TrustedWriteContext(**data)


def _case_payload(ref_or_obj, base):
    if isinstance(ref_or_obj, str):
        return __import__("copy").deepcopy(base[ref_or_obj])
    return __import__("copy").deepcopy(ref_or_obj)


def _case_op(store, op, base):
    kind = op["op"]
    if kind == "add_source":
        seq = store.issue_source_proof() if op.get("proof_after_confirm") else None
        store.add_source(op["kind"], op["id"], op["version"], op["content"], trusted_seq=seq)
    elif kind == "delete_source":
        store.delete_source(op["kind"], op["id"], withdrawn=bool(op.get("withdrawn")))
    elif kind == "ingest":
        store.ingest(_case_payload(op["ref"], base),
                     trusted=_ctx_from_spec(op["trusted"], store) if op.get("trusted") else None)
    elif kind == "add_memory_without_receipt":
        store.add_memory_without_receipt(op["memory_id"], character_id=op["character_id"],
                                         owner_user_id=op.get("owner_user_id"))
    elif kind == "forget_memory":
        store.forget_memory(op["memory_id"])
    elif kind == "retract_memory":
        store.retract_memory(op["memory_id"])
    elif kind == "reallow_memory":
        tok = store.reallow_new_memory(
            op["memory_id"], user_confirmed=op.get("user_confirmed", False),
            trusted=_ctx_from_spec(op["trusted"], store) if op.get("trusted") else None)
        if tok:
            setattr(store, "_cli_last_grant", tok)
    elif kind == "restore_backup":
        store.restore_from_backup(op["memory_id"], _case_payload(op["ref"], base))
    else:
        raise ContractError("unknown setup op")


KNOWN_FIXTURE_OPS = frozenset({"add_source", "ingest", "forget_memory",
    "retract_memory", "reallow_memory", "project", "restore_backup"})
SUCCESS_RESULTS = frozenset({"registered", "accepted", "projected", "forgotten",
                             "retracted", "granted", "restored"})


class FixtureError(ValueError):
    """合成夹具结构错误；只回稳定类型，不回显正文。"""


def _get_path(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _validate_fixture(fx):
    if not isinstance(fx, dict):
        raise FixtureError("fixture_not_object")
    if fx.get("schema") != "provenance-shadow-synthetic-fixture-v1":
        raise FixtureError("unknown_fixture_schema")
    events = fx.get("events")
    if not isinstance(events, list) or not events:
        raise FixtureError("events_missing_or_empty")
    for index, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise FixtureError("event_not_object")
        if not isinstance(ev.get("id"), str) or not ev["id"]:
            raise FixtureError("event_id_invalid")
        op = ev.get("op")
        if op not in KNOWN_FIXTURE_OPS:
            raise FixtureError("unknown_op")
        if op == "add_source":
            src = ev.get("source")
            if not isinstance(src, dict) or not isinstance(src.get("kind"), str) \
                    or not isinstance(src.get("id"), int) or not isinstance(src.get("version"), str):
                raise FixtureError("source_shape_invalid")
            if "proof" in ev and not isinstance(ev.get("proof"), bool):
                raise FixtureError("proof_not_bool")
        if op == "ingest":
            if ev.get("trusted") is not None and not isinstance(ev.get("trusted"), dict):
                raise FixtureError("trusted_not_object")
            if ev.get("expect_result") is not None and not isinstance(ev.get("expect_result"), str):
                raise FixtureError("expect_result_invalid")
        if op in ("forget_memory", "retract_memory", "reallow_memory") and \
                not isinstance(ev.get("memory_id"), int):
            raise FixtureError("memory_id_invalid")
    return events


def _run_legacy_cases(store_factory, data):
    base = data["base_envelopes"]
    out = []
    for case in data["cases"]:
        store = store_factory()
        detail = {"case": case["id"], "dim": case.get("dim", ""),
                  "op": case.get("action", {}).get("op")}
        try:
            for op in case.get("setup", []) + case.get("pre", []):
                _case_op(store, op, base)
            action, expect = case["action"], case.get("expect", {})
            kind = action["op"]
            if kind == "parse":
                try:
                    parse_envelope(__import__("copy").deepcopy(action["payload"]))
                    detail.update(result="unexpected_accept", ok=False)
                except Exception as e:  # noqa: BLE001
                    detail.update(result=type(e).__name__,
                                  ok=(type(e).__name__ == expect.get("error")))
            elif kind == "build_actor":
                actor = build_actor_from_request(claimed_actor_kind=action.get("claimed"),
                                                 request_authenticated_user_id=action.get("request_user"))
                ok = all(actor.get(k) == v for k, v in expect.get("actor", {}).items())
                schema_validate(actor, "actor")
                detail.update(result="built", ok=ok)
            elif kind == "order":
                ordered = order_receipts(action["items"])
                got = [x.get("record_id") for x in ordered]
                detail.update(result=got, ok=got == expect.get("order"))
            elif kind == "project":
                if "error" in expect:
                    try:
                        store.project(action["memory_id"],
                                      viewer_user_id=action["viewer_user_id"],
                                      scope_user_ids=set(action["scope_user_ids"]),
                                      viewer_character_id=action["viewer_character_id"],
                                      mode=action.get("mode", "family"))
                        detail.update(result="unexpected_accept", ok=False)
                    except Exception as e:  # noqa: BLE001
                        detail.update(result=type(e).__name__, ok=(type(e).__name__ == expect["error"]))
                else:
                    projection = store.project(action["memory_id"],
                                               viewer_user_id=action["viewer_user_id"],
                                               scope_user_ids=set(action["scope_user_ids"]),
                                               viewer_character_id=action["viewer_character_id"],
                                               mode=action.get("mode", "family"))
                    bad = []
                    for dotted, want in expect.get("project", {}).items():
                        got, found = _get_path(projection, dotted)
                        if not found or got != want:
                            bad.append({"field": dotted, "want": want, "got": got if found else None})
                    detail.update(result="projected", evidence=projection["evidence"]["state"],
                                  receipt_state=projection["receipt_state"],
                                  projector_mismatch=bad, ok=not bad)
            elif kind == "ingest":
                result = store.ingest(_case_payload(action["ref"], base),
                                      trusted=_ctx_from_spec(action["trusted"], store)
                                      if action.get("trusted") else None)
                detail.update(result=result, ok=result == expect.get("result", "accepted"))
            else:
                detail.update(result="unknown_action", ok=False)
        except Exception as e:  # noqa: BLE001
            detail.update(result=type(e).__name__, ok=False)
        out.append(detail)
    return out


def _source_has_proof(store, payload):
    src = (payload or {}).get("source") or {}
    kind, sid = src.get("kind"), src.get("id")
    if kind not in ("chat_message", "diary_entry", "moment", "phone_snapshot"):
        # M1没有派生来源的可信证明提供者，保持隔离，不借unknown绕过证明。
        return False
    item = store.sources.get((kind, sid))
    return bool(item and item.get("proof_seq") is not None)


def _run_adapter(store_factory, fixture):
    events_spec = _validate_fixture(fixture)
    store = store_factory()
    events = []
    last_grant = None
    for spec in events_spec:
        op = spec["op"]
        row = {"id": spec.get("id"), "op": op, "result": None, "ok": False}
        try:
            if op == "add_source":
                seq = store.issue_source_proof() if spec.get("proof") else None
                store.add_source(spec["source"]["kind"], spec["source"]["id"],
                                 spec["source"]["version"], spec.get("content", ""),
                                 trusted_seq=seq)
                row["result"] = "registered"
            elif op == "ingest":
                trusted = _ctx_from_spec(spec.get("trusted"), store) if spec.get("trusted") else None
                payload = _case_payload(spec.get("wire"), fixture.get("base_envelopes", {}))
                if trusted is None:
                    row["result"] = "refused_untrusted_wire"
                elif not _source_has_proof(store, payload):
                    row["result"] = "quarantined_missing_proof"
                else:
                    row["result"] = store.ingest(payload, trusted=trusted)
            elif op == "forget_memory":
                store.forget_memory(spec["memory_id"]); row["result"] = "forgotten"
            elif op == "retract_memory":
                store.retract_memory(spec["memory_id"]); row["result"] = "retracted"
            elif op == "reallow_memory":
                trusted = _ctx_from_spec(spec.get("trusted"), store) if spec.get("trusted") else None
                tok = store.reallow_new_memory(spec["memory_id"], trusted=trusted)
                row["result"] = "granted" if tok else "refused"
                if tok:
                    last_grant = tok
                    store._cli_last_grant = tok
            elif op == "project":
                projection = store.project(spec["memory_id"],
                                           viewer_user_id=spec["viewer_user_id"],
                                           scope_user_ids=set(spec["scope_user_ids"]),
                                           viewer_character_id=spec["viewer_character_id"],
                                           mode=spec.get("mode", "family"))
                row["result"] = "projected"
                row["evidence_state"] = projection["evidence"]["state"]
                row["projection"] = g5_read_only_draft(projection)
                # CLI仅输出状态证明；正文不进入落盘报告（不改变内核读投影）。
                row["projection"]["evidence"]["excerpt"] = None
            elif op == "restore_backup":
                row["result"] = store.restore_from_backup(
                    spec["memory_id"], _case_payload(spec["ref"], fixture.get("base_envelopes", {})))
            expected = spec.get("expect_result")
            if expected is not None:
                row["ok"] = row["result"] == expected
                row["expected"] = expected
            else:
                row["ok"] = row["result"] in SUCCESS_RESULTS
        except Exception as e:  # noqa: BLE001
            row["result"] = type(e).__name__
            # 仅允许预期的合同拒绝；编程错误不能被夹具声明为通过。
            expected = spec.get("expect_result")
            if isinstance(e, ContractError) and expected is not None:
                row["expected"] = expected
                row["ok"] = row["result"] == expected
            else:
                row["ok"] = False
        events.append(row)
    counts = {}
    for row in events:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    accepted = sum(1 for r in events if r.get("op") == "ingest" and r.get("result") == "accepted")
    successful_actions = sum(1 for r in events if r.get("result") in SUCCESS_RESULTS)
    refused = sum(1 for r in events if r.get("result", "").startswith("refused"))
    quarantined = sum(1 for r in events if r.get("result", "").startswith("quarantined"))
    matched = sum(1 for r in events if r.get("expected") is not None and r.get("ok"))
    unexpected = [r for r in events if not r.get("ok")]
    serialized = json.dumps(events, ensure_ascii=False)
    sensitive = []
    for ev in fixture.get("events", []):
        if isinstance(ev.get("content"), str):
            sensitive.append(ev["content"])
    sensitive = [x for x in sensitive if x]
    return {"events": events, "counts": counts,
            "processed": len(events), "accepted": accepted, "successful_actions": successful_actions,
            "refused": refused, "quarantined": quarantined,
            "matched_expected": matched, "unexpected": len(unexpected),
            "last_grant_present": bool(last_grant),
            "sensitive_fields_absent": all(x not in serialized for x in sensitive),
            "sensitive_values_checked": len(sensitive)}


def main(argv=None) -> int:
    """合成适配 CLI：--fixture 显式合成 JSON；只读，不写业务库/不 emit。"""
    import argparse
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from provenance_shadow_reference import DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if a.out and pathlib.Path(a.fixture).resolve() == pathlib.Path(a.out).resolve():
        print(json.dumps({"ok": False, "error": "output_overwrites_fixture"}))
        return 2
    try:
        fixture = json.loads(pathlib.Path(a.fixture).read_text(encoding="utf-8"))
        adapter = _run_adapter(ProvenanceStore, fixture)
    except FixtureError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(e).__name__}, ensure_ascii=False))
        return 2
    legacy = _run_legacy_cases(ProvenanceStore, DATA)
    report = {
        "synthetic": True,
        "note": "trusted context is offline synthetic; not real authentication; no DB/emit",
        "adapter": adapter,
        "legacy_cases": {"total": len(legacy),
                         "ok": sum(1 for x in legacy if x.get("ok")),
                         "failed": [x for x in legacy if not x.get("ok")]},
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.out:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if (adapter["unexpected"] == 0 and adapter["sensitive_fields_absent"]
                 and report["legacy_cases"]["failed"] == []) else 1


if __name__ == "__main__":
    raise SystemExit(main())
