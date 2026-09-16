# -*- coding: utf-8 -*-
"""小批次任务 2（2026-09-16）：`[GEN_IMAGE]` 生图 prompt 存量清洗脚本测试。

脚本从磁盘 importlib 加载（scripts/ 不是 Python 包，沿用项目既有惯例）。
覆盖：
1. 纯函数 clean_content：标记变体剥净、IMG_TEXT 文案保留、裸 prompt 不回流、
   extra_meta 文案回退、中性兜底、幂等；
2. 与 app.agent.actions（P1-4）同口径：清洗后零标记、零 prompt；
3. 临时库（tmp_path）端到端：dry-run 只读、--apply 先备份再写、重复执行命中为 0。
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

_PROMPT = "傍晚的厨房，灶上一只砂锅咕嘟炖着红烧肉，暖黄灯光，生活感插画风格"
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cleanup_gen_image_content.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup_gen_image_content", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cleanup = _load_script()

_SCHEMA = """
CREATE TABLE chat_messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL,
  sender_type VARCHAR(10) NOT NULL,
  content TEXT NOT NULL,
  image_url VARCHAR(500),
  extra_meta TEXT,
  is_read BOOLEAN NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _mk_db(tmp_path: Path, rows: list[tuple]) -> Path:
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    con.executemany(
        "INSERT INTO chat_messages (id, session_id, sender_type, content, extra_meta) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()
    return db


# ────────────────── 1：纯函数清洗口径 ──────────────────

def test_只带prompt的行需要中性回退():
    res = cleanup.clean_content(f"[GEN_IMAGE] {_PROMPT}")
    assert res["new_content"] == cleanup.NEUTRAL_FALLBACK
    assert res["source"] == "neutral"
    assert res["changed"] is True
    assert _PROMPT not in res["new_content"]


def test_gen_image与img_text并存时保留文案():
    content = f"行，等着。\n[IMG_TEXT]锅里给你留着。\n[GEN_IMAGE] {_PROMPT}"
    res = cleanup.clean_content(content)
    assert res["new_content"] == "行，等着。\n锅里给你留着。"
    assert res["source"] == "img_text" and res["from_img_text"] is True
    assert "GEN_IMAGE" not in res["new_content"].upper()
    assert "IMG_TEXT" not in res["new_content"].upper()
    assert _PROMPT not in res["new_content"]


def test_全角空白大小写漏闭合变体都剥净():
    for raw in (
        f"行。[GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]",
        f"行。【GEN_IMAGE】{_PROMPT}【/GEN_IMAGE】",
        f"行。[ GEN_IMAGE ] {_PROMPT}",
        f"行。【gen_image】{_PROMPT}",
        f"行。\n[GEN_IMAGE] {_PROMPT}",          # 漏写闭合
        f"[IMG_TEXT]这回准能看见！[GEN_IMAGE] {_PROMPT}[GEN_IMAGE]",  # 闭合被写成开标签
    ):
        res = cleanup.clean_content(raw)
        assert _PROMPT not in res["new_content"], raw
        assert "GEN_IMAGE" not in res["new_content"].upper(), raw
        assert "IMG_TEXT" not in res["new_content"].upper(), raw


def test_配文照抄prompt时不回流不误留():
    content = f"[IMG_TEXT]{_PROMPT}[/IMG_TEXT][GEN_IMAGE]{_PROMPT}[/GEN_IMAGE]"
    res = cleanup.clean_content(content)
    assert res["new_content"] == cleanup.NEUTRAL_FALLBACK
    assert _PROMPT not in res["new_content"]


def test_extra_meta文案回退且prompt键不回流():
    content = f"[GEN_IMAGE] {_PROMPT}"
    ok = cleanup.clean_content(content, json.dumps({"img_text": "给你看看它。"}))
    assert ok["new_content"] == "给你看看它。" and ok["source"] == "extra_meta"
    # prompt / 描述绝不作回退（与「prompt 只进 meta」同口径）
    bad = cleanup.clean_content(content, json.dumps({"prompt": _PROMPT, "tools": ["生图"]}))
    assert bad["new_content"] == cleanup.NEUTRAL_FALLBACK
    assert bad["source"] == "neutral"


def test_清洗幂等_二次清洗零改动():
    content = f"行，等着。\n[IMG_TEXT]锅里给你留着。\n[GEN_IMAGE] {_PROMPT}"
    once = cleanup.clean_content(content)["new_content"]
    twice = cleanup.clean_content(once)
    assert twice["changed"] is False
    assert twice["new_content"] == once


def test_无标记正文逐字节不变():
    text = "行，走两步就行。"
    res = cleanup.clean_content(text)
    assert res["changed"] is False and res["new_content"] == text


def test_与canonical_actions口径一致():
    from app.agent import actions

    content = f"[IMG_TEXT]锅里给你留着。\n[GEN_IMAGE] {_PROMPT}"
    clean, prompt, img_text = actions.extract_gen_image(content)
    assert prompt == _PROMPT and img_text == "锅里给你留着。"
    res = cleanup.clean_content(content)
    assert _PROMPT not in res["new_content"]
    assert "GEN_IMAGE" not in res["new_content"].upper()
    assert img_text in res["new_content"]


# ────────────────── 2：plan_cleanup 范围 ──────────────────

def _rows():
    return [
        {"id": 1, "session_id": 1, "content": f"[GEN_IMAGE] {_PROMPT}", "extra_meta": None},
        {"id": 2, "session_id": 1, "content": "行。[IMG_TEXT] 就这张。", "extra_meta": None},
        {"id": 3, "session_id": 1, "content": "普通一句。", "extra_meta": None},
    ]


def test_默认只处理gen_image行():
    decisions = cleanup.plan_cleanup(_rows())
    assert [d["id"] for d in decisions] == [1]
    assert decisions[0]["scope"] == "gen_image"


def test_可选追加img_text_only行():
    decisions = cleanup.plan_cleanup(_rows(), include_img_text_only=True)
    assert [d["id"] for d in decisions] == [1, 2]
    assert decisions[1]["scope"] == "img_text_only"
    assert decisions[1]["new_content"] == "行。就这张。"


# ────────────────── 3：临时库端到端（tmp_path）──────────────────

def _seed_db(tmp_path: Path) -> Path:
    return _mk_db(tmp_path, [
        (11490, 11, "ai", f"忙就忙你的。\n[IMG_TEXT]锅里给你留着。\n[GEN_IMAGE] {_PROMPT}", None),
        (11522, 11, "ai", "[GEN_IMAGE] 一只圆滚滚的金棕色小仓鼠，暖黄灯光，插画风格", None),
        (8719, 11, "ai", "画我？行啊。【IMG_TEXT】画丑了不收。", None),
    ])


def test_dry_run只读不改库(tmp_path):
    db = _seed_db(tmp_path)
    before = sqlite3.connect(db).execute(
        "SELECT content FROM chat_messages WHERE id=11490").fetchone()[0]
    rc = cleanup.main(["--db", str(db), "--backup-dir", str(tmp_path / "bk")])
    assert rc == 0
    after = sqlite3.connect(db).execute(
        "SELECT content FROM chat_messages WHERE id=11490").fetchone()[0]
    assert after == before
    assert not (tmp_path / "bk").exists()


def test_apply先备份再写且幂等(tmp_path):
    db = _seed_db(tmp_path)
    bk = tmp_path / "bk"
    rc = cleanup.main(["--db", str(db), "--apply", "--backup-dir", str(bk)])
    assert rc == 0

    backups = list(bk.glob("gen_image_content_backup_*.json"))
    assert len(backups) == 1
    saved = json.loads(backups[0].read_text(encoding="utf-8"))
    assert sorted(r["id"] for r in saved) == [11490, 11522]
    # 备份保留原 content（可回滚）
    assert any(_PROMPT in (r["content"] or "") for r in saved)

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    c11490 = con.execute("SELECT content FROM chat_messages WHERE id=11490").fetchone()["content"]
    c11522 = con.execute("SELECT content FROM chat_messages WHERE id=11522").fetchone()["content"]
    c8719 = con.execute("SELECT content FROM chat_messages WHERE id=8719").fetchone()["content"]
    assert c11490 == "忙就忙你的。\n锅里给你留着。"
    assert c11522 == cleanup.NEUTRAL_FALLBACK
    assert c8719 == "画我？行啊。【IMG_TEXT】画丑了不收。"   # 默认不处理 img_text-only
    assert _PROMPT not in c11490 and _PROMPT not in c11522

    # 幂等：重复执行命中为 0、不再产生改动
    rows = cleanup.load_rows(con)
    assert cleanup.plan_cleanup(rows) == []
    con.close()


def test_extra_meta始终不动(tmp_path):
    db = _mk_db(tmp_path, [
        (7, 1, "ai", f"[GEN_IMAGE] {_PROMPT}", json.dumps({"prompt": _PROMPT, "tools": ["生图"]})),
    ])
    assert cleanup.main(["--db", str(db), "--apply", "--backup-dir", str(tmp_path / "bk")]) == 0
    con = sqlite3.connect(db)
    meta = con.execute("SELECT extra_meta FROM chat_messages WHERE id=7").fetchone()[0]
    assert json.loads(meta)["prompt"] == _PROMPT
    con.close()
