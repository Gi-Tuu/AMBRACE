# -*- coding: utf-8 -*-
"""G3/G4 原型 v3 契约测试（返工2：关闭 wire/旧代际/重新允许三条重放路径）。"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import unittest
import sys
import subprocess
import tempfile

import jsonschema
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import prototype_provenance_shadow as pc

HERE = pathlib.Path(__file__).parent
from provenance_shadow_reference import DATA, BASE
SCHEMA = json.loads((pathlib.Path(__file__).resolve().parents[1] / "scripts" / "provenance_shadow_contract.schema.json").read_text(encoding="utf-8"))
SNIPPET = "SYNTHETIC_REVOKED_SNIPPET"
REASON = "SYNTHETIC_SOURCE_REASON"
CTX = pc.TrustedWriteContext(actor_kind="authenticated_user", actor_user_id=7,
                             resource_owner_user_id=7, request_authenticated=True)


def _payload(ref_or_obj):
    if isinstance(ref_or_obj, str):
        return copy.deepcopy(BASE[ref_or_obj])
    return copy.deepcopy(ref_or_obj)


def _ctx(spec, store=None):
    data = dict(spec)
    grant_memory = data.pop("grant_for_memory", None)
    if grant_memory is not None and store is not None:
        for token, grant in store.grants.items():
            if grant.get("memory_id") == grant_memory and not grant.get("used"):
                data["reallow_grant"] = token
                break
    return pc.TrustedWriteContext(**data)


def _get_path(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


_MISSING = object()


class CaseRunner(unittest.TestCase):
    def test_all_cases(self):
        for case in DATA["cases"]:
            with self.subTest(case=case["id"], dim=case["dim"]):
                self._run(case)

    def _run(self, case):
        store = pc.ProvenanceStore()
        for op in case.get("setup", []) + case.get("pre", []):
            self._op(store, op)
        action, expect = case["action"], case.get("expect", {})
        kind = action["op"]
        if kind == "parse":
            with self.assertRaises(getattr(pc, expect["error"])):
                pc.parse_envelope(copy.deepcopy(action["payload"]))
        elif kind == "build_actor":
            actor = pc.build_actor_from_request(
                claimed_actor_kind=action.get("claimed"),
                request_authenticated_user_id=action.get("request_user"))
            for dotted, want in expect["actor"].items():
                self.assertEqual(_get_path(actor, dotted), want)
            pc.schema_validate(actor, "actor")
        elif kind == "order":
            ordered = pc.order_receipts(action["items"])
            self.assertEqual([x["record_id"] for x in ordered], expect["order"])
        elif kind == "project":
            self._project(store, action, expect)
        elif kind == "ingest":
            result = store.ingest(_payload(action["ref"]),
                                  trusted=_ctx(action["trusted"], store)
                                  if action.get("trusted") else None)
            self.assertEqual(result, expect.get("result", "accepted"))
        else:
            self.fail(f"unknown action {kind}")
        for check in case.get("assert", []):
            self._assert(store, check)

    def _project(self, store, action, expect):
        kwargs = dict(viewer_user_id=action["viewer_user_id"],
                      scope_user_ids=set(action["scope_user_ids"]),
                      viewer_character_id=action["viewer_character_id"],
                      mode=action.get("mode", "family"))
        if "error" in expect:
            with self.assertRaises(getattr(pc, expect["error"])):
                store.project(action["memory_id"], **kwargs)
            return
        projection = store.project(action["memory_id"], **kwargs)
        for dotted, want in expect["project"].items():
            self.assertEqual(_get_path(projection, dotted), want, dotted)
        pc.schema_validate(projection, "projection")
        draft = pc.g5_read_only_draft(projection)
        pc.schema_validate(draft, "g5_draft")
        text = json.dumps(draft, ensure_ascii=False)
        for word in ["detail_json", "source_id", "actor_id", "user_id",
                     "derived_from_ids", "content"]:
            self.assertNotIn(word, text)

    def _assert(self, store, check):
        if check["check"] == "no_plaintext":
            persisted = json.dumps(store.receipts, ensure_ascii=False)
        elif check["check"] == "late_no_plaintext":
            persisted = json.dumps(store._late, ensure_ascii=False)
        else:
            self.fail(f"unknown check {check['check']}")
        for needle in check["needles"]:
            self.assertNotIn(needle, persisted)

    def _op(self, store, op):
        kind = op["op"]
        if kind == "add_source":
            seq = store.issue_source_proof() if op.get("proof_after_confirm") else None
            store.add_source(op["kind"], op["id"], op["version"], op["content"],
                             trusted_seq=seq)
        elif kind == "delete_source":
            store.delete_source(op["kind"], op["id"], withdrawn=bool(op.get("withdrawn")))
        elif kind == "ingest":
            store.ingest(_payload(op["ref"]),
                         trusted=_ctx(op["trusted"], store)
                         if op.get("trusted") else None)
        elif kind == "add_memory_without_receipt":
            store.add_memory_without_receipt(
                op["memory_id"], character_id=op["character_id"],
                owner_user_id=op.get("owner_user_id"))
        elif kind == "forget_memory":
            store.forget_memory(op["memory_id"])
        elif kind == "retract_memory":
            store.retract_memory(op["memory_id"])
        elif kind == "reallow_memory":
            store.reallow_new_memory(
                op["memory_id"], user_confirmed=op.get("user_confirmed", False),
                trusted=_ctx(op["trusted"], store) if op.get("trusted") else None)
        elif kind == "restore_backup":
            store.restore_from_backup(op["memory_id"], _payload(op["ref"]))
        else:
            self.fail(f"unknown setup op {kind}")


class BoundaryContracts(unittest.TestCase):
    """返工2 三条边界的独立正反例（不依赖数据文件）。"""

    def test_wire_without_trusted_cannot_create_visible_resource(self):
        store = pc.ProvenanceStore()
        source = BASE["legacy_wire"]["source"]
        store.add_source(source["kind"], source["id"], source["version"], SNIPPET)
        self.assertEqual(store.ingest(BASE["legacy_wire"]), "refused_untrusted_wire")
        self.assertEqual(store.memories, {})
        with self.assertRaises(pc.Gone):
            store.project(9001, viewer_user_id=7, scope_user_ids={7},
                          viewer_character_id=11)
        self.assertNotIn(SNIPPET, json.dumps(store._late))

    def test_boolean_confirmation_only_records_request(self):
        store, value = _seeded()
        store.forget_memory(9001)
        self.assertTrue(store.reallow_new_memory(9001, user_confirmed=True))
        replay = copy.deepcopy(value)
        replay["idempotency_key"] += ":replay"
        replay["record_id"] += ":replay"
        self.assertEqual(store.ingest(replay, trusted=CTX), "refused_tombstoned")
        with self.assertRaises(pc.Gone):
            store.project(9001, viewer_user_id=7, scope_user_ids={7},
                          viewer_character_id=11)
        self.assertFalse(store.tombstones[9001].get("state") == "reallowed")

    def test_trusted_confirmation_requires_owner_and_grant(self):
        store, value = _seeded()
        store.forget_memory(9001)
        stranger = pc.TrustedWriteContext(actor_kind="authenticated_user", actor_user_id=8,
                                          resource_owner_user_id=8, request_authenticated=True)
        self.assertFalse(store.reallow_new_memory(9001, trusted=stranger))
        token = store.reallow_new_memory(9001, trusted=CTX)
        self.assertTrue(token)
        # 旧同源事件：即使新 key 也在撤销快照里，拒绝
        replay = copy.deepcopy(value)
        replay["idempotency_key"] += ":old"
        replay["record_id"] += ":old"
        self.assertEqual(store.ingest(replay, trusted=CTX), "refused_stale_source")
        # 确认后新增的源：缺 grant 拒绝，带 grant 接受
        store.add_source("chat_message", 2001, "n1", "NEW_SOURCE",
                         trusted_seq=store.issue_source_proof())
        renewed = copy.deepcopy(value)
        renewed.update(record_id="R-new", idempotency_key="IDEM-NEW")
        renewed["source"] = {"kind": "chat_message", "id": 2001,
                             "version": "n1", "generation": 1}
        renewed["evidence"] = {"retention": "none", "excerpt": None,
                               "excerpt_sha256": None, "source_version": None}
        self.assertEqual(store.ingest(renewed, trusted=CTX), "refused_no_grant")
        grant_ctx = pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=7,
            resource_owner_user_id=7, request_authenticated=True,
            reallow_grant=token)
        self.assertEqual(store.ingest(renewed, trusted=grant_ctx), "accepted")
        projection = store.project(9001, viewer_user_id=7, scope_user_ids={7},
                                   viewer_character_id=11)
        self.assertEqual(projection["receipt_state"], "present")
        self.assertNotEqual(projection["evidence"]["excerpt"], SNIPPET)

    def test_other_preexisting_source_is_still_old_event(self):
        store, value = _seeded()
        source_b = {"kind": "chat_message", "id": 1002, "version": "b1", "generation": 1}
        store.add_source(source_b["kind"], source_b["id"], source_b["version"], SNIPPET)
        store.forget_memory(9001)
        self.assertTrue(store.reallow_new_memory(9001, trusted=CTX))
        delayed = copy.deepcopy(value)
        delayed.update(record_id="R-delayed-B", idempotency_key="IDEM-DELAYED-B")
        delayed["source"] = source_b
        self.assertEqual(store.ingest(delayed, trusted=CTX), "refused_stale_source")
        with self.assertRaises(pc.Gone):
            store.project(9001, viewer_user_id=7, scope_user_ids={7},
                          viewer_character_id=11)

    def test_grant_is_single_use(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token = store.reallow_new_memory(9001, trusted=CTX)
        store.add_source("chat_message", 2001, "n1", "NEW_SOURCE",
                         trusted_seq=store.issue_source_proof())
        renewed = copy.deepcopy(value)
        renewed.update(record_id="R-new", idempotency_key="IDEM-NEW")
        renewed["source"] = {"kind": "chat_message", "id": 2001,
                             "version": "n1", "generation": 1}
        renewed["evidence"] = {"retention": "none", "excerpt": None,
                               "excerpt_sha256": None, "source_version": None}
        grant_ctx = pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=7,
            resource_owner_user_id=7, request_authenticated=True,
            reallow_grant=token)
        self.assertEqual(store.ingest(renewed, trusted=grant_ctx), "accepted")
        # 再次遗忘后，用过的 grant 不得复用；新 grant 才可进入下一个新源
        store.forget_memory(9001)
        store.add_source("chat_message", 2002, "n2", "NEW_SOURCE_2",
                         trusted_seq=store.issue_source_proof())
        second = copy.deepcopy(renewed)
        second.update(record_id="R-new-2", idempotency_key="IDEM-NEW-2")
        second["source"] = {"kind": "chat_message", "id": 2002,
                            "version": "n2", "generation": 1}
        self.assertEqual(store.ingest(second, trusted=grant_ctx), "refused_tombstoned")


    def test_late_registered_source_without_proof_is_rejected(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token = store.reallow_new_memory(9001, trusted=CTX)
        store.add_source("chat_message", 2002, "n2", SNIPPET)  # 无来源证明
        renewed = copy.deepcopy(value)
        renewed.update(record_id="R-late", idempotency_key="IDEM-LATE")
        renewed["source"] = {"kind": "chat_message", "id": 2002,
                             "version": "n2", "generation": 1}
        renewed["evidence"] = {"retention": "none", "excerpt": None,
                               "excerpt_sha256": None, "source_version": None}
        grant_ctx = pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=7,
            resource_owner_user_id=7, request_authenticated=True,
            reallow_grant=token)
        self.assertEqual(store.ingest(renewed, trusted=grant_ctx),
                         "refused_unproven_source")
        self.assertNotIn(SNIPPET, json.dumps(store.receipts))
        self.assertNotIn(SNIPPET, json.dumps(store._late))

    def test_grant_scope_mismatch_not_consumed(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token = store.reallow_new_memory(9001, trusted=CTX)
        store.add_source("chat_message", 2003, "n3", "NEW_SOURCE_3",
                         trusted_seq=store.issue_source_proof())
        renewed = copy.deepcopy(value)
        renewed.update(record_id="R-scope", idempotency_key="IDEM-SCOPE")
        renewed["source"] = {"kind": "chat_message", "id": 2003,
                             "version": "n3", "generation": 1}
        renewed["evidence"] = {"retention": "none", "excerpt": None,
                               "excerpt_sha256": None, "source_version": None}
        wrong = pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=8,
            resource_owner_user_id=8, request_authenticated=True,
            reallow_grant=token)
        self.assertEqual(store.ingest(renewed, trusted=wrong), "refused_owner_scope")
        self.assertFalse(store.grants[token]["used"])
        right = pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=7,
            resource_owner_user_id=7, request_authenticated=True,
            reallow_grant=token)
        self.assertEqual(store.ingest(renewed, trusted=right), "accepted")


    # ── 返工5 验收矩阵：reallowed / reallowed_active 两状态 ──
    def _scene(self):
        store, value = _seeded()
        store.issue_source_proof(); store.issue_source_proof()  # 撤销前已有可信事件
        store.forget_memory(9001)                                # revoked_seq=2
        store.issue_source_proof()                               # 撤销与确认之间的事件
        token = store.reallow_new_memory(9001, trusted=CTX)      # confirm_seq=3
        return store, value, token

    def _event(self, store, value, *, source_id=3001, character_id=11,
               proof="new", key="matrix-key"):
        envelope = copy.deepcopy(value)
        envelope.update(record_id=key, idempotency_key=key, character_id=character_id)
        envelope["source"] = {"kind": "chat_message", "id": source_id,
                              "version": "x1", "generation": 1}
        envelope["evidence"] = {"retention": "none", "excerpt": None,
                                "excerpt_sha256": None, "source_version": None}
        if proof == "new":
            seq = store.issue_source_proof()
        elif proof is None:
            seq = None
        else:
            seq = proof
        store.add_source("chat_message", source_id, "x1", "MATRIX_SOURCE",
                         trusted_seq=seq)
        return envelope

    def _ctx(self, token=None, owner=7, character=None):
        return pc.TrustedWriteContext(
            actor_kind="authenticated_user", actor_user_id=owner,
            resource_owner_user_id=owner, request_authenticated=True,
            reallow_grant=token)

    def _active_scene(self):
        store, value, token = self._scene()
        fresh = self._event(store, value, key="first-recovery")
        self.assertEqual(store.ingest(fresh, trusted=self._ctx(token)), "accepted")
        return store, value, token

    def _snapshot(self, store):
        return {
            "receipts": json.dumps(store.receipts, ensure_ascii=False, sort_keys=True),
            "late": json.dumps(store._late, ensure_ascii=False, sort_keys=True),
            "owner": (store.memories.get(9001) or {}).get("owner_user_id"),
            "state": (store.memories.get(9001) or {}).get("status"),
            "idem": set(store._idem),
            "grants": {k: (v.get("used"), v.get("invalidated"))
                       for k, v in store.grants.items()},
        }

    def _expect_case(self, state, name, store, value, token, envelope, ctx, expected):
        before = self._snapshot(store)
        result = store.ingest(envelope, trusted=ctx)
        self.assertEqual(result, expected)
        if expected != "accepted":
            after = self._snapshot(store)
            self.assertEqual(after["receipts"], before["receipts"])
            self.assertEqual(after["late"], before["late"])
            self.assertEqual(after["owner"], before["owner"])
            self.assertEqual(after["state"], before["state"])
            self.assertNotIn(SNIPPET, after["receipts"])
            self.assertNotIn(SNIPPET, after["late"])
            self.assertEqual(after["idem"], before["idem"])
            self.assertEqual(after["grants"], before["grants"])

    def test_matrix_reallowed_state(self):
        specs = [
            ("owner-wrong", dict(source_id=3010), dict(owner=8), "refused_owner_scope"),
            ("character-wrong", dict(source_id=3011, character_id=12), {}, "refused_character_scope"),
            ("no-proof", dict(source_id=3012, proof=None), {}, "refused_unproven_source"),
            ("proof-old", dict(source_id=3013, proof="revoked"), {}, "refused_stale_source"),
            ("proof-between", dict(source_id=3014, proof="confirm"), {}, "refused_not_after_confirmation"),
            ("proof-new", dict(source_id=3015), {}, "accepted"),
            ("grant-forged", dict(source_id=3016), dict(token="grant-" + "f" * 32),
             "refused_no_grant"),
        ]
        for name, event_kw, ctx_kw, expected in specs:
            with self.subTest(state="reallowed", case=name):
                store, value, token = self._scene()
                revoked = store.tombstones[9001]["revoked_seq"]
                confirm = store.tombstones[9001]["confirm_seq"]
                kw = dict(event_kw)
                if kw.get("proof") == "revoked":
                    kw["proof"] = revoked
                elif kw.get("proof") == "confirm":
                    kw["proof"] = confirm
                envelope = self._event(store, value, key=name, **kw)
                grant_token = ctx_kw["token"] if "token" in ctx_kw else token
                ctx = self._ctx(grant_token, owner=ctx_kw.get("owner", 7))
                self._expect_case("reallowed", name, store, value, token,
                                  envelope, ctx, expected)

    def test_matrix_active_state(self):
        specs = [
            ("owner-wrong", dict(source_id=3020), dict(owner=8), "refused_owner_scope"),
            ("character-wrong", dict(source_id=3021, character_id=12), {}, "refused_character_scope"),
            ("no-proof", dict(source_id=3022, proof=None), {}, "refused_unproven_source"),
            ("proof-old", dict(source_id=3023, proof="revoked"), {}, "refused_stale_source"),
            ("proof-between", dict(source_id=3024, proof="confirm"), {}, "refused_not_after_confirmation"),
            ("grant-reuse", dict(source_id=3025), dict(use_token=True), "refused_grant_reuse"),
            ("legal-update", dict(source_id=3026), {}, "accepted"),
        ]
        for name, event_kw, ctx_kw, expected in specs:
            with self.subTest(state="reallowed_active", case=name):
                store, value, token = self._active_scene()
                revoked = store.tombstones[9001]["revoked_seq"]
                confirm = store.tombstones[9001]["confirm_seq"]
                kw = dict(event_kw)
                if kw.get("proof") == "revoked":
                    kw["proof"] = revoked
                elif kw.get("proof") == "confirm":
                    kw["proof"] = confirm
                envelope = self._event(store, value, key=name, **kw)
                grant_token = token if ctx_kw.get("use_token") else ctx_kw.get("token")
                ctx = self._ctx(grant_token, owner=ctx_kw.get("owner", 7))
                before = self._snapshot(store)
                result = store.ingest(envelope, trusted=ctx)
                self.assertEqual(result, expected)
                self.assertEqual((store.memories.get(9001) or {}).get("owner_user_id"), 7)
                if expected != "accepted":
                    after = self._snapshot(store)
                    self.assertEqual(after["receipts"], before["receipts"])
                    self.assertEqual(after["late"], before["late"])
                    self.assertEqual(after["idem"], before["idem"])
                    self.assertNotIn(SNIPPET, after["receipts"])
                    self.assertNotIn(SNIPPET, after["late"])


    def test_repeated_confirmation_invalidates_previous_grant(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token1 = store.reallow_new_memory(9001, trusted=CTX)
        token2 = store.reallow_new_memory(9001, trusted=CTX)  # 无来源事件插入
        self.assertNotEqual(token1, token2)
        self.assertTrue(store.grants[token1].get("invalidated"))
        self.assertFalse(store.grants[token2].get("used"))
        envelope = self._event(store, value, source_id=4001, key="epoch-retry")
        before = self._snapshot(store)
        self.assertNotEqual(store.ingest(envelope, trusted=self._ctx(token1)), "accepted")
        after = self._snapshot(store)
        self.assertEqual(after["receipts"], before["receipts"])
        self.assertEqual(after["late"], before["late"])
        self.assertEqual(after["idem"], before["idem"])
        self.assertFalse(store.grants[token2].get("used"))
        self.assertEqual(store.ingest(envelope, trusted=self._ctx(token2)), "accepted")
        self.assertEqual((store.memories.get(9001) or {}).get("owner_user_id"), 7)

    def test_old_round_token_cannot_be_reused(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token1 = store.reallow_new_memory(9001, trusted=CTX)
        store.forget_memory(9001)
        token2 = store.reallow_new_memory(9001, trusted=CTX)
        self.assertNotEqual(token1, token2)
        envelope = self._event(store, value, source_id=4002, key="old-round")
        self.assertNotEqual(store.ingest(envelope, trusted=self._ctx(token1)), "accepted")
        self.assertFalse(store.grants[token2].get("used"))
        self.assertEqual(store.ingest(envelope, trusted=self._ctx(token2)), "accepted")

    def test_second_confirmation_after_source_event_also_invalidates(self):
        store, value = _seeded()
        store.forget_memory(9001)
        token1 = store.reallow_new_memory(9001, trusted=CTX)
        store.issue_source_proof()  # 两次确认之间有来源事件（seq 变化）
        token2 = store.reallow_new_memory(9001, trusted=CTX)
        self.assertNotEqual(store.tombstones[9001]["confirmation_epoch"], 0)
        self.assertTrue(store.grants[token1].get("invalidated"))
        envelope = self._event(store, value, source_id=4003, key="seq-change")
        self.assertNotEqual(store.ingest(envelope, trusted=self._ctx(token1)), "accepted")
        self.assertEqual(store.ingest(envelope, trusted=self._ctx(token2)), "accepted")

    def test_typed_source_requires_positive_generation(self):
        payload = copy.deepcopy(BASE["trusted_create"])
        payload["source"]["generation"] = None
        with self.assertRaises(pc.ContractError):
            pc.parse_envelope(payload)


class SchemaAndRulesTest(unittest.TestCase):
    def test_schema_is_draft7_and_builder_output_validates(self):
        jsonschema.Draft7Validator.check_schema(SCHEMA)
        actor = pc.build_actor_from_request(claimed_actor_kind="model",
                                            request_authenticated_user_id=7)
        jsonschema.Draft7Validator(SCHEMA["definitions"]["actor"]).validate(actor)
        self.assertNotIn("claimed_ignored", actor)

    def test_source_event_proof_schema_is_real(self):
        schema = json.loads((pathlib.Path(__file__).resolve().parents[1] / "scripts" / "provenance_shadow_contract.schema.json").read_text(encoding="utf-8"))
        proof = {"kind": "source_service", "sequence": 42,
                 "source_kind": "chat_message", "source_id": 2001}
        wrapper = {"$schema": schema["$schema"], "definitions": schema["definitions"],
                   "allOf": [{"$ref": "#/definitions/source_event_proof"}]}
        jsonschema.Draft7Validator(wrapper).validate(proof)
        forged = {"kind": "wire", "sequence": 999}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft7Validator(wrapper).validate(forged)

    def test_trusted_context_schema_is_real_and_grant_shaped(self):
        schema = json.loads((pathlib.Path(__file__).resolve().parents[1] / "scripts" / "provenance_shadow_contract.schema.json").read_text(encoding="utf-8"))
        context = {
            "actor_kind": "authenticated_user", "actor_user_id": 7,
            "resource_owner_user_id": 7, "request_authenticated": True,
            "reallow_grant": "grant-" + "a" * 32,
        }
        wrapper = {"$schema": schema["$schema"], "definitions": schema["definitions"],
                   "allOf": [{"$ref": "#/definitions/trusted_write_context"}]}
        jsonschema.Draft7Validator(wrapper).validate(context)
        bad = dict(context, reallow_grant="wire-forged")
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft7Validator(wrapper).validate(bad)

    def test_all_base_envelopes_pass_real_schema_and_parse(self):
        for name, payload in BASE.items():
            with self.subTest(name=name):
                pc.parse_envelope(payload)

    def test_naive_time_as_utc_and_bad_time_rejected(self):
        self.assertEqual(pc.to_utc("2026-09-25T12:00:00+08:00", "t"),
                         pc.to_utc("2026-09-25T04:00:00", "t"))
        with self.assertRaises(pc.ContractError):
            pc.to_utc("2026-09-25 12:00", "t")


def _seeded():
    store = pc.ProvenanceStore()
    value = copy.deepcopy(BASE["trusted_create"])
    source = value["source"]
    store.add_source(source["kind"], source["id"], source["version"], SNIPPET)
    store.ingest(value, trusted=CTX)
    return store, value




class AdapterTests(unittest.TestCase):
    def test_reference_and_schema_are_self_contained(self):
        self.assertTrue(BASE)
        self.assertTrue(DATA["cases"])
        self.assertEqual(SCHEMA.get("$schema"), "http://json-schema.org/draft-07/schema#")
        self.assertNotIn("rework6_delivery", json.dumps(DATA)[:2000])

    def test_adapter_ingest_project_and_reject_roundtrip(self):
        store = pc.ProvenanceStore()
        value = copy.deepcopy(BASE["trusted_create"])
        source = value["source"]
        store.add_source(source["kind"], source["id"], source["version"], SNIPPET)
        receipt = store.ingest(value, trusted=CTX)
        self.assertIsInstance(receipt, str)
        projection = store.project(value["memory_id"], viewer_user_id=7,
                                   scope_user_ids={7},
                                   viewer_character_id=value["character_id"],
                                   mode="family")
        self.assertIn("receipt_state", projection)
        self.assertIn("evidence", projection)
        bad_version = copy.deepcopy(BASE["trusted_create"])
        bad_version["v"] = 99
        with self.assertRaises(pc.UnsupportedVersion):
            pc.parse_envelope(bad_version)


class CLIAdapterTests(unittest.TestCase):
    def _fixture(self, base):
        env = copy.deepcopy(base["trusted_create"])
        env["source"] = {"kind": "chat_message", "id": 2001, "version": "v1",
                         "generation": 1}
        return {"schema": "provenance-shadow-synthetic-fixture-v1",
                "base_envelopes": {"trusted_create": env, "legacy_wire": base["legacy_wire"]},
                "events": [
                    {"id": "s1", "op": "add_source",
                     "source": {"kind": "chat_message", "id": 2001, "version": "v1"},
                     "content": "SYNTHETIC_CLI_SENTINEL", "proof": True},
                    {"id": "ok", "op": "ingest", "wire": "trusted_create",
                     "trusted": {"actor_kind": "authenticated_user", "actor_user_id": 7,
                                 "resource_owner_user_id": 7, "request_authenticated": True}},
                    {"id": "forged", "op": "ingest", "wire": "legacy_wire", "trusted": None,
                     "expect_result": "refused_untrusted_wire"},
                    {"id": "proj", "op": "project", "memory_id": 9001,
                     "viewer_user_id": 7, "scope_user_ids": [7], "viewer_character_id": 11},
                ]}

    def _run(self, fixture):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "fixture.json"
            out = pathlib.Path(td) / "report.json"
            f.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parents[1]
                                                     / "scripts" / "prototype_provenance_shadow.py"),
                                "--fixture", str(f), "--out", str(out)],
                               capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, timeout=60)
            report = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
            return r, report

    def test_cli_executes_adapter_and_all_38_legacy_cases(self):
        r, report = self._run(self._fixture({"trusted_create": BASE["trusted_create"],
                                             "legacy_wire": BASE["legacy_wire"]}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(report["legacy_cases"]["total"], 38)
        self.assertEqual(report["legacy_cases"]["ok"], 38)
        counts = report["adapter"]["counts"]
        self.assertEqual(counts.get("accepted"), 1)
        self.assertEqual(counts.get("refused_untrusted_wire"), 1)
        self.assertTrue(report["adapter"]["sensitive_fields_absent"])

    def test_cli_bad_json_is_nonzero_and_does_not_echo_raw(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "bad.json"
            f.write_text('{"secret":"DO_NOT_ECHO"', encoding="utf-8")
            r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parents[1]
                                                     / "scripts" / "prototype_provenance_shadow.py"),
                                "--fixture", str(f)], capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("DO_NOT_ECHO", r.stdout + r.stderr)


class StrictCLIAndRunnerTests(unittest.TestCase):
    def _run_cli(self, fixture):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "fixture.json"
            out = pathlib.Path(td) / "report.json"
            f.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parents[1]
                                                     / "scripts" / "prototype_provenance_shadow.py"),
                                "--fixture", str(f), "--out", str(out)],
                               capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, timeout=60)
            report = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
            return r, report

    def test_missing_proof_is_quarantined_and_explicit_negative_passes(self):
        base = copy.deepcopy(BASE["trusted_create"])
        base["source"] = {"kind": "chat_message", "id": 2001, "version": "v1", "generation": 1}
        fx = {"schema": "provenance-shadow-synthetic-fixture-v1",
              "base_envelopes": {"trusted_create": base},
              "events": [
                  {"id": "s", "op": "add_source",
                   "source": {"kind": "chat_message", "id": 2001, "version": "v1"},
                   "content": "SYNTHETIC_MISSING_PROOF", "proof": False},
                  {"id": "i", "op": "ingest", "wire": "trusted_create",
                   "trusted": {"actor_kind": "authenticated_user", "actor_user_id": 7,
                               "resource_owner_user_id": 7, "request_authenticated": True},
                   "expect_result": "quarantined_missing_proof"},
              ]}
        r, report = self._run_cli(fx)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(report["adapter"]["counts"].get("quarantined_missing_proof"), 1)
        self.assertGreaterEqual(report["adapter"]["sensitive_values_checked"], 1)

    def test_empty_or_unknown_schema_events_are_nonzero(self):
        for fx in ({}, {"schema": "v0", "events": []},
                   {"schema": "provenance-shadow-synthetic-fixture-v1", "events": []}):
            r, _ = self._run_cli(fx)
            self.assertNotEqual(r.returncode, 0)

    def test_legacy_project_expectation_mutation_is_detected(self):
        data = copy.deepcopy(DATA)
        mutated = 0
        for case in data["cases"]:
            if case["action"]["op"] == "project" and "project" in case.get("expect", {}):
                case["expect"]["project"]["receipt_state"] = "IMPOSSIBLE_REVIEW_EXPECTATION"
                mutated += 1
        rows = pc._run_legacy_cases(pc.ProvenanceStore, data)
        bad = [r for r in rows if not r.get("ok")]
        self.assertGreaterEqual(mutated, 9)
        self.assertGreaterEqual(len(bad), mutated)





class PrimaryLifecycleCLIContracts(unittest.TestCase):
    _fixture = CLIAdapterTests._fixture
    _run = CLIAdapterTests._run
    def _good(self):
        fx = self._fixture(BASE)
        fx["events"] = fx["events"][:2]
        return fx

    def test_restore_is_isolated_and_expected_read_denial_is_valid(self):
        fx = self._good()
        fx["events"] += [
            {"id": "restore", "op": "restore_backup", "memory_id": 9001,
             "ref": "trusted_create", "expect_result": "restored_unverified"},
            {"id": "denied", "op": "project", "memory_id": 9001,
             "viewer_user_id": 7, "scope_user_ids": [7], "viewer_character_id": 11,
             "expect_result": "NotVisible"}]
        r, report = self._run(fx)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(report["adapter"]["events"][-1]["result"], "NotVisible")
        self.assertNotIn("projection", report["adapter"]["events"][-1])

    def test_forget_retract_late_and_new_source_grant(self):
        for revoke in ("forget_memory", "retract_memory"):
            with self.subTest(revoke=revoke):
                fx = self._good()
                new = copy.deepcopy(fx["base_envelopes"]["trusted_create"])
                new["record_id"] = "R-NEW"
                new["idempotency_key"] = "K-NEW"
                new["source"] = {"kind": "chat_message", "id": 2002,
                                 "version": "v2", "generation": 1}
                fx["base_envelopes"]["new"] = new
                trusted = {"actor_kind": "authenticated_user", "actor_user_id": 7,
                           "resource_owner_user_id": 7, "request_authenticated": True}
                fx["events"] += [
                    {"id": "revoke", "op": revoke, "memory_id": 9001},
                    {"id": "late", "op": "ingest", "wire": "trusted_create",
                     "trusted": trusted, "expect_result": "refused_tombstoned"},
                    {"id": "confirm", "op": "reallow_memory", "memory_id": 9001,
                     "trusted": trusted},
                    {"id": "src2", "op": "add_source", "proof": True,
                     "source": {"kind": "chat_message", "id": 2002, "version": "v2"},
                     "content": "SYNTHETIC_NEW_CONTENT"},
                    {"id": "new", "op": "ingest", "wire": "new",
                     "trusted": dict(trusted, use_last_grant=True)},
                    {"id": "view", "op": "project", "memory_id": 9001,
                     "viewer_user_id": 7, "scope_user_ids": [7], "viewer_character_id": 11}]
                r, report = self._run(fx)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(report["adapter"]["counts"].get("accepted"), 2)
                self.assertEqual(report["adapter"]["accepted"], 2)
                self.assertEqual(report["adapter"]["counts"].get("refused_tombstoned"), 1)

    def test_stale_generation_is_not_laundered_by_new_key(self):
        fx = self._good()
        trusted = fx["events"][1]["trusted"]
        new = copy.deepcopy(fx["base_envelopes"]["trusted_create"])
        new["record_id"] = "R-STALE"; new["idempotency_key"] = "K-STALE"
        fx["base_envelopes"]["stale"] = new
        fx["events"] += [
            {"id": "reuse", "op": "add_source", "proof": True,
             "source": {"kind": "chat_message", "id": 2001, "version": "v2"},
             "content": "NEW_CONTENT"},
            {"id": "late", "op": "ingest", "wire": "stale", "trusted": trusted,
             "expect_result": "refused_stale_generation"}]
        r, report = self._run(fx)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(report["adapter"]["accepted"], 1)

    def test_wrong_expected_error_and_programming_error_do_not_pass(self):
        fx = self._good()
        fx["events"] += [{"id": "v", "op": "project", "memory_id": 9001,
                          "viewer_user_id": 8, "scope_user_ids": [8],
                          "viewer_character_id": 11, "expect_result": "Gone"}]
        r, _ = self._run(fx)
        self.assertNotEqual(r.returncode, 0)
        fx["events"][-1] = {"id": "broken", "op": "project", "expect_result": "KeyError"}
        r, _ = self._run(fx)
        self.assertNotEqual(r.returncode, 0)

    def test_output_does_not_overwrite_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "fixture.json"
            f.write_text(json.dumps(self._good()), encoding="utf-8")
            before = f.read_bytes()
            script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "prototype_provenance_shadow.py"
            r = subprocess.run([sys.executable, str(script), "--fixture", str(f), "--out", str(f)],
                               capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}, timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(f.read_bytes(), before)


    def test_unknown_source_is_not_an_unproven_escape_hatch(self):
        fx = self._good()
        value = fx["base_envelopes"]["trusted_create"]
        value["source"] = {"kind": "unknown", "id": None, "version": None}
        fx["events"][1]["expect_result"] = "quarantined_missing_proof"
        r, report = self._run(fx)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(report["adapter"]["accepted"], 0)
        self.assertEqual(report["adapter"]["quarantined"], 1)

    def test_current_excerpt_is_removed_from_cli_report(self):
        import hashlib
        fx = self._good()
        sentinel = "REVIEW_PRIVATE_EXCERPT_7246"
        fx["events"][0]["content"] = sentinel
        value = fx["base_envelopes"]["trusted_create"]
        value["evidence"].update(excerpt=sentinel,
            excerpt_sha256=hashlib.sha256(sentinel.encode()).hexdigest(), source_version="v1")
        fx["events"] += [{"id": "view", "op": "project", "memory_id": 9001,
                          "viewer_user_id": 7, "scope_user_ids": [7], "viewer_character_id": 11}]
        r, report = self._run(fx)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(sentinel, json.dumps(report))
        self.assertEqual(report["adapter"]["events"][-1]["evidence_state"], "current")
        self.assertIsNone(report["adapter"]["events"][-1]["projection"]["evidence"]["excerpt"])


if __name__ == "__main__":
    unittest.main()
