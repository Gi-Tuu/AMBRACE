"""G3/G4 M1 自包含合成案例参考（来源 rework6 prototype/synthetic_cases.json）。

只含合成数据，不读真实回执/业务库；保留原 JSON 语义，仅改为 Python 模块以便候选自包含。
"""
import json
_RAW = r"""{
 "synthetic": true,
 "note": "全合成短文本/虚构ID；不拷贝真实聊天或私有评测语料；仅契约原型自测。",
 "base_envelopes": {
  "trusted_create": {
   "v": 1,
   "record_id": "R-create-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-CREATE-1"
  },
  "legacy_wire": {
   "v": 1,
   "record_id": "R-legacy-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-LEGACY-1"
  },
  "family_update": {
   "v": 1,
   "record_id": "R-family-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "update",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T06:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_merged",
    "text": "family edit",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-FAMILY-1"
  },
  "older_event": {
   "v": 1,
   "record_id": "R-older-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "update",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T05:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-OLDER-1"
  },
  "newer_event": {
   "v": 1,
   "record_id": "R-newer-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "update",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T06:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_merged"
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-NEWER-1"
  },
  "no_owner": {
   "v": 1,
   "record_id": "R-noowner-1",
   "character_id": 11,
   "memory_id": 9003,
   "action": "downgrade",
   "source": {
    "kind": "unknown",
    "id": null,
    "version": null,
    "generation": null
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "unknown"
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "unknown",
    "id": null
   },
   "reason": {
    "code": "unknown"
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-NOOWNER-1"
  },
  "diary_collision": {
   "v": 1,
   "record_id": "R-diary-1",
   "character_id": 11,
   "memory_id": 9004,
   "action": "create",
   "source": {
    "kind": "diary_entry",
    "id": 1001,
    "version": "d1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "diary synthetic",
    "excerpt_sha256": "4284b2f041ccf6874b5b4d7d42b9ab7de62cf22c3ccdb6788eda0723a3e31125",
    "source_version": "d1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-DIARY-1"
  },
  "restore_payload": {
   "v": 1,
   "record_id": "R-backup-1",
   "character_id": 11,
   "memory_id": 9005,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-BACKUP-1"
  },
  "reallowed_create": {
   "v": 1,
   "record_id": "R-reallowed-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-REALLOW-1"
  },
  "renewed_create": {
   "v": 1,
   "record_id": "R-renewed-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 3
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-RENEW-1"
  },
  "replay_old": {
   "v": 1,
   "record_id": "R-replay-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1001,
    "version": "m1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T04:05:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "ephemeral_verified",
    "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
    "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
    "source_version": "m1"
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-REPLAY-1"
  },
  "new_source_create": {
   "v": 1,
   "record_id": "R-new-source-1",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 2001,
    "version": "n1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-NEW-SOURCE-1"
  },
  "delayed_b": {
   "v": 1,
   "record_id": "R-delayed-B",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 1002,
    "version": "b1",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-DELAYED-B"
  },
  "late_index_event": {
   "v": 1,
   "record_id": "R-late-index",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 2002,
    "version": "n2",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-LATE-INDEX"
  },
  "owner_mismatch_event": {
   "v": 1,
   "record_id": "R-owner-mismatch",
   "character_id": 11,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 2003,
    "version": "n3",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-OWNER-MISMATCH"
  },
  "wrong_character_event": {
   "v": 1,
   "record_id": "R-wrong-char",
   "character_id": 12,
   "memory_id": 9001,
   "action": "create",
   "source": {
    "kind": "chat_message",
    "id": 2004,
    "version": "n4",
    "generation": 1
   },
   "captured_at": "2026-09-25T04:05:00Z",
   "source_event_at": "2026-09-25T07:00:00Z",
   "actor": {
    "kind": "authenticated_user",
    "user_id": 7,
    "authenticated": true
   },
   "executor": {
    "kind": "service",
    "name": "memory_write"
   },
   "content_subject": {
    "kind": "user",
    "id": 7
   },
   "reason": {
    "code": "user_corrected",
    "text": "SYNTHETIC_SOURCE_REASON",
    "text_visible": true
   },
   "evidence": {
    "retention": "none",
    "excerpt": null,
    "excerpt_sha256": null,
    "source_version": null
   },
   "derivation": {
    "parent_ids": []
   },
   "idempotency_key": "IDEM-WRONG-CHAR"
  }
 },
 "cases": [
  {
   "id": "R5-v2",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": {
     "v": 2,
     "record_id": "R-create-1",
     "character_id": 11,
     "memory_id": 9001,
     "action": "create",
     "source": {
      "kind": "chat_message",
      "id": 1001,
      "version": "m1",
      "generation": 1
     },
     "captured_at": "2026-09-25T04:05:00Z",
     "source_event_at": "2026-09-25T04:05:00Z",
     "actor": {
      "kind": "authenticated_user",
      "user_id": 7,
      "authenticated": true
     },
     "executor": {
      "kind": "service",
      "name": "memory_write"
     },
     "content_subject": {
      "kind": "user",
      "id": 7
     },
     "reason": {
      "code": "user_corrected",
      "text": "SYNTHETIC_SOURCE_REASON",
      "text_visible": true
     },
     "evidence": {
      "retention": "ephemeral_verified",
      "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
      "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
      "source_version": "m1"
     },
     "derivation": {
      "parent_ids": []
     },
     "idempotency_key": "IDEM-CREATE-1"
    }
   },
   "expect": {
    "error": "UnsupportedVersion"
   }
  },
  {
   "id": "R5-bad-json",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": "{bad json"
   },
   "expect": {
    "error": "ContractError"
   }
  },
  {
   "id": "R5-missing-executor",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": {
     "v": 1,
     "record_id": "R-create-1",
     "character_id": 11,
     "memory_id": 9001,
     "action": "create",
     "source": {
      "kind": "chat_message",
      "id": 1001,
      "version": "m1",
      "generation": 1
     },
     "captured_at": "2026-09-25T04:05:00Z",
     "source_event_at": "2026-09-25T04:05:00Z",
     "actor": {
      "kind": "authenticated_user",
      "user_id": 7,
      "authenticated": true
     },
     "content_subject": {
      "kind": "user",
      "id": 7
     },
     "reason": {
      "code": "user_corrected",
      "text": "SYNTHETIC_SOURCE_REASON",
      "text_visible": true
     },
     "evidence": {
      "retention": "ephemeral_verified",
      "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
      "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
      "source_version": "m1"
     },
     "derivation": {
      "parent_ids": []
     },
     "idempotency_key": "IDEM-CREATE-1"
    }
   },
   "expect": {
    "error": "ContractError"
   }
  },
  {
   "id": "R5-missing-generation",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": {
     "v": 1,
     "record_id": "R-create-1",
     "character_id": 11,
     "memory_id": 9001,
     "action": "create",
     "source": {
      "kind": "chat_message",
      "id": 1001,
      "version": "m1"
     },
     "captured_at": "2026-09-25T04:05:00Z",
     "source_event_at": "2026-09-25T04:05:00Z",
     "actor": {
      "kind": "authenticated_user",
      "user_id": 7,
      "authenticated": true
     },
     "executor": {
      "kind": "service",
      "name": "memory_write"
     },
     "content_subject": {
      "kind": "user",
      "id": 7
     },
     "reason": {
      "code": "user_corrected",
      "text": "SYNTHETIC_SOURCE_REASON",
      "text_visible": true
     },
     "evidence": {
      "retention": "ephemeral_verified",
      "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
      "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
      "source_version": "m1"
     },
     "derivation": {
      "parent_ids": []
     },
     "idempotency_key": "IDEM-CREATE-1"
    }
   },
   "expect": {
    "error": "ContractError"
   }
  },
  {
   "id": "R5-actor-unknown-field",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": {
     "v": 1,
     "record_id": "R-create-1",
     "character_id": 11,
     "memory_id": 9001,
     "action": "create",
     "source": {
      "kind": "chat_message",
      "id": 1001,
      "version": "m1",
      "generation": 1
     },
     "captured_at": "2026-09-25T04:05:00Z",
     "source_event_at": "2026-09-25T04:05:00Z",
     "actor": {
      "kind": "model",
      "claimed_user_id": 7
     },
     "executor": {
      "kind": "service",
      "name": "memory_write"
     },
     "content_subject": {
      "kind": "user",
      "id": 7
     },
     "reason": {
      "code": "user_corrected",
      "text": "SYNTHETIC_SOURCE_REASON",
      "text_visible": true
     },
     "evidence": {
      "retention": "ephemeral_verified",
      "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
      "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
      "source_version": "m1"
     },
     "derivation": {
      "parent_ids": []
     },
     "idempotency_key": "IDEM-CREATE-1"
    }
   },
   "expect": {
    "error": "ContractError"
   }
  },
  {
   "id": "R5-root-unknown-field",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "parse",
    "payload": {
     "v": 1,
     "record_id": "R-create-1",
     "character_id": 11,
     "memory_id": 9001,
     "action": "create",
     "source": {
      "kind": "chat_message",
      "id": 1001,
      "version": "m1",
      "generation": 1
     },
     "captured_at": "2026-09-25T04:05:00Z",
     "source_event_at": "2026-09-25T04:05:00Z",
     "actor": {
      "kind": "authenticated_user",
      "user_id": 7,
      "authenticated": true
     },
     "executor": {
      "kind": "service",
      "name": "memory_write"
     },
     "content_subject": {
      "kind": "user",
      "id": 7
     },
     "reason": {
      "code": "user_corrected",
      "text": "SYNTHETIC_SOURCE_REASON",
      "text_visible": true
     },
     "evidence": {
      "retention": "ephemeral_verified",
      "excerpt": "SYNTHETIC_REVOKED_SNIPPET",
      "excerpt_sha256": "4027d7e84679b123173a89f66b1ff41467f5126b1983e1d480c79f4430838d64",
      "source_version": "m1"
     },
     "derivation": {
      "parent_ids": []
     },
     "idempotency_key": "IDEM-CREATE-1",
     "extra_field": 1
    }
   },
   "expect": {
    "error": "ContractError"
   }
  },
  {
   "id": "R5-builder-noauth",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "build_actor",
    "claimed": "authenticated_user",
    "request_user": null
   },
   "expect": {
    "actor": {
     "kind": "unknown"
    }
   }
  },
  {
   "id": "R5-builder-ignores-claim",
   "dim": "schema",
   "setup": [],
   "pre": [],
   "action": {
    "op": "build_actor",
    "claimed": "model",
    "request_user": 7
   },
   "expect": {
    "actor": {
     "kind": "authenticated_user",
     "user_id": 7,
     "authenticated": true
    }
   }
  },
  {
   "id": "R1-family-shared-trusted",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 8,
    "scope_user_ids": [
     7,
     8
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "receipt_state": "present"
    }
   }
  },
  {
   "id": "R1-unowned-family-denied",
   "dim": "trust",
   "setup": [],
   "pre": [
    {
     "op": "add_memory_without_receipt",
     "memory_id": 9988,
     "character_id": 11,
     "owner_user_id": null
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9988,
    "viewer_user_id": 77,
    "scope_user_ids": [
     77
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "error": "NotVisible"
   }
  },
  {
   "id": "R1-user-mode-other",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 8,
    "scope_user_ids": [
     8
    ],
    "viewer_character_id": 11,
    "mode": "user"
   },
   "expect": {
    "error": "NotVisible"
   }
  },
  {
   "id": "R1-cross-character",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 12,
    "mode": "family"
   },
   "expect": {
    "error": "NotVisible"
   }
  },
  {
   "id": "R1-model-context",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "model",
      "resource_owner_user_id": 7
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "receipt_state": "present",
     "actor.kind": "model"
    }
   }
  },
  {
   "id": "R2-forget-late",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "error": "Gone"
   }
  },
  {
   "id": "R2-retract-late",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "retract_memory",
     "memory_id": 9001
    },
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "error": "Gone"
   }
  },
  {
   "id": "R2-reallow-without-confirm",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "user_confirmed": false
    },
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "error": "Gone"
   }
  },
  {
   "id": "R4-cross-timezone",
   "dim": "time",
   "setup": [],
   "pre": [],
   "action": {
    "op": "order",
    "items": [
     {
      "record_id": "later",
      "source_event_at": "2026-09-25T05:00:00Z",
      "captured_at": "2026-09-25T06:01:00Z"
     },
     {
      "record_id": "earlier",
      "source_event_at": "2026-09-25T12:00:00+08:00",
      "captured_at": "2026-09-25T06:00:00Z"
     }
    ]
   },
   "expect": {
    "order": [
     "earlier",
     "later"
    ]
   }
  },
  {
   "id": "R4-equal-tie-break",
   "dim": "time",
   "setup": [],
   "pre": [],
   "action": {
    "op": "order",
    "items": [
     {
      "record_id": "b",
      "source_event_at": "2026-09-25T05:00:00Z",
      "captured_at": "2026-09-25T06:00:00Z"
     },
     {
      "record_id": "a",
      "source_event_at": "2026-09-25T05:00:00Z",
      "captured_at": "2026-09-25T06:00:00Z"
     }
    ]
   },
   "expect": {
    "order": [
     "a",
     "b"
    ]
   }
  },
  {
   "id": "R4-late-older-not-replace",
   "dim": "time",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "ingest",
     "ref": "newer_event",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "ingest",
     "ref": "older_event",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "reason.code": "user_merged"
    }
   }
  },
  {
   "id": "R3-source-delete-scrubs",
   "dim": "scrub",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "delete_source",
     "kind": "chat_message",
     "id": 1001
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "evidence.state": "source_deleted",
     "evidence.excerpt": null,
     "reason.text": null
    }
   },
   "assert": [
    {
     "check": "no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET",
      "SYNTHETIC_SOURCE_REASON"
     ]
    }
   ]
  },
  {
   "id": "R3-late-quarantine-scrubs",
   "dim": "scrub",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "delete_source",
     "kind": "chat_message",
     "id": 1001
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "trusted_create",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true
    }
   },
   "expect": {
    "result": "quarantined"
   },
   "assert": [
    {
     "check": "late_no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET",
      "SYNTHETIC_SOURCE_REASON"
     ]
    }
   ]
  },
  {
   "id": "R1-generation-reuse",
   "dim": "generation",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "delete_source",
     "kind": "chat_message",
     "id": 1001
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "UNRELATED_SYNTHETIC_SOURCE"
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "replay_old",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true
    }
   },
   "expect": {
    "result": "refused_stale_generation"
   },
   "assert": [
    {
     "check": "no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET"
     ]
    }
   ]
  },
  {
   "id": "R1-tag-collision",
   "dim": "generation",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "chat synthetic"
    },
    {
     "op": "add_source",
     "kind": "diary_entry",
     "id": 1001,
     "version": "d1",
     "content": "diary synthetic"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "diary_collision",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "delete_source",
     "kind": "chat_message",
     "id": 1001
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9004,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "evidence.state": "current"
    }
   }
  },
  {
   "id": "R5-missing-receipt",
   "dim": "evidence",
   "setup": [],
   "pre": [
    {
     "op": "add_memory_without_receipt",
     "memory_id": 9006,
     "character_id": 11,
     "owner_user_id": 7
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9006,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "receipt_state": "missing",
     "reason.state": "unknown",
     "actor.kind": "unknown"
    }
   }
  },
  {
   "id": "R5-no-owner-receipt",
   "dim": "evidence",
   "setup": [],
   "pre": [
    {
     "op": "add_memory_without_receipt",
     "memory_id": 9003,
     "character_id": 11,
     "owner_user_id": 7
    },
    {
     "op": "ingest",
     "ref": "no_owner",
     "trusted": {
      "actor_kind": "model",
      "resource_owner_user_id": 7
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9003,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "project": {
     "receipt_state": "present",
     "actor.kind": "model",
     "evidence.state": "unverified"
    }
   }
  },
  {
   "id": "R5-backup-isolated",
   "dim": "backup",
   "setup": [],
   "pre": [
    {
     "op": "restore_backup",
     "memory_id": 9005,
     "ref": "restore_payload"
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9005,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11,
    "mode": "family"
   },
   "expect": {
    "error": "NotVisible"
   }
  },
  {
   "id": "R1-wire-refused",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [],
   "action": {
    "op": "ingest",
    "ref": "legacy_wire"
   },
   "expect": {
    "result": "refused_untrusted_wire"
   },
   "assert": [
    {
     "check": "late_no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET",
      "SYNTHETIC_SOURCE_REASON"
     ]
    }
   ]
  },
  {
   "id": "R1-wire-project-gone",
   "dim": "trust",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "legacy_wire"
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "error": "Gone"
   }
  },
  {
   "id": "R1-generation-project",
   "dim": "generation",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "delete_source",
     "kind": "chat_message",
     "id": 1001
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "UNRELATED_SYNTHETIC_SOURCE"
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "project": {
     "evidence.state": "source_changed",
     "evidence.excerpt": null
    }
   }
  },
  {
   "id": "R2-confirm-old-event-refused",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "user_confirmed": true
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "replay_old",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true
    }
   },
   "expect": {
    "result": "refused_tombstoned"
   },
   "assert": [
    {
     "check": "late_no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET",
      "SYNTHETIC_SOURCE_REASON"
     ]
    }
   ]
  },
  {
   "id": "R2-confirm-old-event-gone",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "user_confirmed": true
    },
    {
     "op": "ingest",
     "ref": "replay_old",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "error": "Gone"
   }
  },
  {
   "id": "R2-trusted-reallow-new-source",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2001,
     "version": "n1",
     "content": "NEW_SYNTHETIC_SOURCE",
     "proof_after_confirm": true
    },
    {
     "op": "ingest",
     "ref": "new_source_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true,
      "grant_for_memory": 9001
     }
    }
   ],
   "action": {
    "op": "project",
    "memory_id": 9001,
    "viewer_user_id": 7,
    "scope_user_ids": [
     7
    ],
    "viewer_character_id": 11
   },
   "expect": {
    "project": {
     "receipt_state": "present",
     "evidence.excerpt": null
    }
   }
  },
  {
   "id": "R3-old-different-source-blocked",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "SYNTHETIC_REVOKED_SNIPPET"
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1002,
     "version": "b1",
     "content": "SYNTHETIC_SOURCE_REASON"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "delayed_b",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true
    }
   },
   "expect": {
    "result": "refused_stale_source"
   },
   "assert": [
    {
     "check": "no_plaintext",
     "needles": [
      "SYNTHETIC_REVOKED_SNIPPET",
      "SYNTHETIC_SOURCE_REASON"
     ]
    }
   ]
  },
  {
   "id": "R3-new-source-without-grant",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2001,
     "version": "n1",
     "content": "NEW_SYNTHETIC_SOURCE"
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "new_source_create",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true
    }
   },
   "expect": {
    "result": "refused_no_grant"
   }
  },
  {
   "id": "R4-late-unproven-source",
   "dim": "revoke",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2002,
     "version": "n2",
     "content": "LATE_INDEX_NO_PROOF"
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "late_index_event",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true,
     "grant_for_memory": 9001
    }
   },
   "expect": {
    "result": "refused_unproven_source"
   }
  },
  {
   "id": "R4-grant-owner-mismatch",
   "dim": "grant",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2003,
     "version": "n3",
     "content": "NEW_SOURCE_3",
     "proof_after_confirm": true
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "owner_mismatch_event",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 8,
     "resource_owner_user_id": 8,
     "request_authenticated": true,
     "grant_for_memory": 9001
    }
   },
   "expect": {
    "result": "refused_owner_scope"
   }
  },
  {
   "id": "R4-grant-not-consumed-on-scope-fail",
   "dim": "grant",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2003,
     "version": "n3",
     "content": "NEW_SOURCE_3",
     "proof_after_confirm": true
    },
    {
     "op": "ingest",
     "ref": "owner_mismatch_event",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 8,
      "resource_owner_user_id": 8,
      "request_authenticated": true,
      "grant_for_memory": 9001
     }
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "owner_mismatch_event",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true,
     "grant_for_memory": 9001
    }
   },
   "expect": {
    "result": "accepted"
   }
  },
  {
   "id": "R4-grant-wrong-character",
   "dim": "grant",
   "setup": [
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 1001,
     "version": "m1",
     "content": "S"
    }
   ],
   "pre": [
    {
     "op": "ingest",
     "ref": "trusted_create",
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "forget_memory",
     "memory_id": 9001
    },
    {
     "op": "reallow_memory",
     "memory_id": 9001,
     "trusted": {
      "actor_kind": "authenticated_user",
      "actor_user_id": 7,
      "resource_owner_user_id": 7,
      "request_authenticated": true
     }
    },
    {
     "op": "add_source",
     "kind": "chat_message",
     "id": 2004,
     "version": "n4",
     "content": "NEW_SOURCE_4",
     "proof_after_confirm": true
    }
   ],
   "action": {
    "op": "ingest",
    "ref": "wrong_character_event",
    "trusted": {
     "actor_kind": "authenticated_user",
     "actor_user_id": 7,
     "resource_owner_user_id": 7,
     "request_authenticated": true,
     "grant_for_memory": 9001
    }
   },
   "expect": {
    "result": "refused_character_scope"
   }
  }
 ]
}
"""
DATA = json.loads(_RAW)
BASE = DATA["base_envelopes"]
CASES = DATA["cases"]
