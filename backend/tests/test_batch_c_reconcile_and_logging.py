# -*- coding: utf-8 -*-
"""承重结构批 C（2026-09-26）：额度账本对账 + migrate 日志守卫摘除残留 logger。

守住两件事：
1. **低-3**：`rating_quota.diff_observed()` 用库内「今日已评星」观测值**只报告与账本的差异、绝不写账本**
   （跨日按 0 起算、异常只 WARNING）；`run_ai_rating` 每次执行在选候选之前报告一次。**为什么只报告**：
   观测源会被真实强化路径污染（检索命中 / 主动复习也刷 `last_reinforce_at`、且不看 `ai_rated`），
   自动补齐会把账本一次抬满、当天评星全停摆 —— 与 09-25 那次「换了污染列」的故障同构（见 `diff_observed`）。
   对账属旁路加固：它自己或取数查询炸了，都不许影响评星的返回值 / 写库 / 账本正常累加。
2. **低-4**：`_alembic_logging_guard` 退出时，除逐项还原快照内 logger 外，还摘除 fileConfig 期间
   **新建**的 `alembic*` logger（连同其 handler），不留未收尾的全局状态污染。

账本文件一律重定向到 tmp_path；评星用例走 _dbclone 临时库 + LLM/留痕打桩，绝不碰生产库与生产账本。
"""
import asyncio
import json
import logging
import logging.config
import os
from datetime import datetime, timedelta

import pytest

from _dbclone import clone_engine, make_session_factory

from app.db import migrate
from app.memory import ai_rating as ar
from app.memory import rating_quota as rq
from app.utils.timeutil import beijing_day_start_utc, now_naive_utc

_CN_TZ = rq._CN_TZ
_USER = 1
_CHAR = 13


class _SpyLogger:
    """收 _logger 的 warning/info 文本，其余方法透传真 logger（与 test_ai_rating_trace 同口径）。"""

    def __init__(self, inner, sink):
        self._inner = inner
        self._sink = sink

    def warning(self, msg, *a):
        self._sink.append(("warning", str(msg) % a if a else str(msg)))

    def info(self, msg, *a):
        self._sink.append(("info", str(msg) % a if a else str(msg)))

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ────────────────────────── 账本夹具 ──────────────────────────

@pytest.fixture()
def quota_file(tmp_path, monkeypatch):
    path = tmp_path / "ai_rating_quota.json"
    monkeypatch.setattr(rq, "_STATE_FILE", path)
    return path


def _write_ledger(counts, date=None):
    """直接落一个账本文件（绕过 add，便于构造「跨日」「账本偏高」等前置态）。"""
    rq._STATE_FILE.write_text(
        json.dumps({"date": date or rq._today_key(),
                    "counts": {str(k): v for k, v in counts.items()}}, ensure_ascii=False),
        encoding="utf-8")


def _read_ledger():
    return json.loads(rq._STATE_FILE.read_text(encoding="utf-8"))


# ────────────────────── ① diff_observed 只报账本偏低的差异 ──────────────────────

def test_diff_observed_只报账本偏低的差异(quota_file):
    _write_ledger({13: 3, 14: 5})
    assert rq.diff_observed({13: 7, 14: 2, 15: 1}) == {13: (3, 7), 15: (0, 1)}, \
        "只报「账本 < 观测」（账本没有该角色＝按 0 起算）；账本偏高不报"
    assert rq.used_today(13) == 3, "只报告 ⇒ 账本一行都不许动"
    assert rq.used_today(14) == 5
    assert rq.used_today(15) == 0


def test_diff_observed_无差异时不写盘(quota_file):
    _write_ledger({13: 3})
    before = quota_file.read_text(encoding="utf-8")
    assert rq.diff_observed({13: 3, 99: 0}) == {}
    assert quota_file.read_text(encoding="utf-8") == before, "只读报告，绝无写侧效应"


# ────────────────── ② 账本缺失 / 跨日：按 0 比，且不动账本 ──────────────────

def test_diff_observed_账本缺失按零比且不建账本(quota_file):
    assert not quota_file.exists()
    assert rq.diff_observed({13: 4}) == {13: (0, 4)}
    assert not quota_file.exists(), "只报告 ⇒ 不许顺手替用户建出账本文件"


def test_diff_observed_跨日按零比且不动账本(quota_file):
    yesterday = (datetime.now(_CN_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    _write_ledger({13: 9}, date=yesterday)
    assert rq.used_today(13) == 0, "跨日账本本就该视为空"
    assert rq.diff_observed({13: 2}) == {13: (0, 2)}, "昨天的 9 不得带进今天"
    assert _read_ledger()["date"] == yesterday, "报告口径不得改写账本的日期键"
    assert rq.used_today(13) == 0


# ────────────────── ③ 只读路径：不依赖写盘、容坏值 ──────────────────

def test_diff_observed_不依赖写盘(quota_file, monkeypatch):
    """只报告 ⇒ 报告路径根本不写盘；写盘整体坏掉也不影响出结果（不再有「写失败就谎报」这类分支）。"""
    _write_ledger({13: 3})
    sink = []
    monkeypatch.setattr(rq, "_logger", _SpyLogger(rq._logger, sink))

    def _boom(_data):
        raise RuntimeError("quota disk gone")

    monkeypatch.setattr(rq, "_save", _boom)
    assert rq.diff_observed({13: 7}) == {13: (3, 7)}, "报告路径不写盘，写盘炸了也照样出结果"
    assert not sink, f"只读报告不该产生任何 WARNING：{sink}"
    assert rq.used_today(13) == 3


def test_diff_observed_容忍坏观测值(quota_file):
    _write_ledger({13: 3})
    assert rq.diff_observed({13: None, "abc": 5, 14: "2", "": 7}) == {14: (0, 2)}
    assert rq.diff_observed(None) == {}


# ────────────────── ④ 观测取数 SQL 口径 ──────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """只记 SQL、按需返回行或抛错的假 session（不连任何库）。"""

    def __init__(self, rows=(), boom=None):
        self.rows = list(rows)
        self.boom = boom
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self.boom:
            raise self.boom
        return _FakeResult(self.rows)


def test_observed_today_counts_sql口径():
    sess = _FakeSession(rows=[(_CHAR, 7), (14, 2)])
    assert asyncio.run(ar._observed_today_counts(sess)) == {_CHAR: 7, 14: 2}

    stmt = sess.statements[0]
    sql = str(stmt)
    assert "ai_rated" in sql, "口径必须限定已评星行"
    assert "last_reinforce_at >=" in sql, "时间条件只能落在 last_reinforce_at"
    assert "updated_at" not in sql, "禁止用 updated_at（衰减结算也刷新它＝09-25 停摆根因）"
    assert "GROUP BY" in sql.upper() and "memories.character_id" in sql, "必须按 character_id 分组"
    assert "count(" in sql.lower()

    dts = [v for v in stmt.compile().params.values() if isinstance(v, datetime)]
    assert len(dts) == 1, f"仅一个时间阈值参数：{stmt.compile().params}"
    assert (dts[0] + timedelta(hours=8)).strftime("%H:%M:%S") == "00:00:00", "阈值＝北京今日零点"
    assert abs(beijing_day_start_utc() - dts[0]) <= timedelta(seconds=5), "且已换算成 UTC-naive"


def test_observed_today_counts_查询失败返回空(monkeypatch):
    sink = []
    monkeypatch.setattr(ar, "_logger", _SpyLogger(ar._logger, sink))
    sess = _FakeSession(boom=RuntimeError("no such table: memories"))
    assert asyncio.run(ar._observed_today_counts(sess)) == {}
    assert any(lvl == "warning" and "no such table" in msg for lvl, msg in sink), sink


# ────────────────── ⑤ run_ai_rating 接线 ──────────────────

@pytest.fixture()
def rating_db(monkeypatch, tmp_path):
    """临时库（1 用户 1 角色）+ 账本落 tmp_path + 留痕打桩（不落 agent_task_logs）。"""
    engine = clone_engine(os.path.join(str(tmp_path), "batch_c.db"))
    factory = make_session_factory(engine)
    import app.db.database as db_mod
    import app.agent.trace as trace
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(ar, "async_session_factory", factory)   # 模块级 import，须单独 patch
    monkeypatch.setattr(rq, "_STATE_FILE", tmp_path / "quota.json")
    monkeypatch.setattr(trace, "enqueue_task_log", lambda **_kw: None)

    from app.models.character import AICharacter
    from app.models.user import User

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="bc_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()
    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _mem(mid, content, *, ai_rated=False, reinforced_at=None):
    from app.models.memory import Memory
    return Memory(id=mid, user_id=_USER, character_id=_CHAR, memory_type="event",
                  content=content, title=f"t{mid}", ai_rated=ai_rated,
                  last_reinforce_at=reinforced_at)


def _seed(factory, *rows):
    async def _go():
        async with factory() as db:
            for r in rows:
                db.add(r)
            await db.commit()
    asyncio.run(_go())


@pytest.fixture()
def llm_all_4(monkeypatch):
    """LLM 打桩：把送进来的每条候选都评 4 星（不落真调用）。"""
    import app.agent.llm_client as llm_mod

    async def _fake(messages=None, *_a, **_k):
        body = messages[-1]["content"]
        ids = [int(seg.split("=")[1].split()[0])
               for seg in body.splitlines() if "id=" in seg]
        return json.dumps([{"id": i, "star": 4} for i in ids])

    monkeypatch.setattr(llm_mod, "chat_completion", _fake)


def _run():
    return asyncio.run(ar.run_ai_rating())


@pytest.mark.slow
def test_run_ai_rating_对账只报告不动账本(rating_db, llm_all_4, monkeypatch):
    """库内已有 1 条今日评过的记忆 ⇒ 只打一条差异 INFO；账本仍只记本拍真评的 1 条。"""
    _seed(rating_db,
          _mem(101, "用户说明天要去长沙出差", ai_rated=True, reinforced_at=now_naive_utc()),
          _mem(102, "用户喜欢吃香菜"))
    seen = []
    real = rq.diff_observed
    monkeypatch.setattr(ar, "diff_observed",
                        lambda observed, **kw: (seen.append(dict(observed)), real(observed, **kw))[1])
    sink = []
    monkeypatch.setattr(ar, "_logger", _SpyLogger(ar._logger, sink))

    assert _run() == 1
    assert seen == [{_CHAR: 1}], f"评星开始前必须报告一次差异，且收到库内观测值：{seen}"
    assert rq.used_today(_CHAR) == 1, "账本只记本拍真评的 1 条（观测值不得抬账本）"
    assert any(lvl == "info" and "Rating quota diff" in msg for lvl, msg in sink), \
        f"有差异须打一条 INFO：{sink}"


@pytest.mark.slow
def test_run_ai_rating_观测被强化污染也不动账本(rating_db, llm_all_4):
    """回归（批次 C 的拍板依据）：观测口径会被真实强化污染 —— 老评星行只要今天被检索/复习碰到
    就算进观测值。本批刻意只用它**报告**；若哪天真按观测补齐账本，这条会红：账本被一次抬满 ⇒
    本拍一条都评不了（＝09-25 那次停摆换了个污染列）。"""
    _seed(rating_db, *[_mem(200 + i, f"记忆{i}", ai_rated=True, reinforced_at=now_naive_utc())
                       for i in range(ar.AI_RATING_MAX_PER_CHAR)] +
          [_mem(230, "还没评过的记忆")])
    assert rq.used_today(_CHAR) == 0, "前置：账本丢了信息"
    assert _run() == 1, "观测被污染不得影响本拍评星（账本不许被抬满）"
    assert rq.used_today(_CHAR) == 1


@pytest.mark.slow
def test_run_ai_rating_对账炸了不影响评星(rating_db, llm_all_4, monkeypatch):
    _seed(rating_db, _mem(102, "用户喜欢吃香菜"))

    def _boom(_observed, **_kw):
        raise RuntimeError("diff down")

    monkeypatch.setattr(ar, "diff_observed", _boom)
    sink = []
    monkeypatch.setattr(ar, "_logger", _SpyLogger(ar._logger, sink))

    assert _run() == 1, "对账是旁路加固，炸了也不得阻断评星"
    assert rq.used_today(_CHAR) == 1, "正常评星累加不受对账失败影响"
    assert any(lvl == "warning" and "diff down" in msg for lvl, msg in sink), sink


# ────────────────── ⑥ migrate 日志守卫 ──────────────────

_TEST_INI = """\
[loggers]
keys = root,alembicnew,appmem

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_alembicnew]
level = INFO
handlers = console
qualname = alembic.runtime.migration

[logger_appmem]
level = WARN
handlers =
qualname = app.memory.ai_rating

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(message)s
"""


def test_日志守卫摘掉fileConfig新建的alembic_logger(tmp_path):
    """守卫进入前不存在、fileConfig 期间新建的 alembic* logger ⇒ 退出后不得留在 loggerDict。"""
    name = "alembic.runtime.migration"
    app_name = "app.memory.ai_rating"
    manager = logging.Logger.manager
    held = manager.loggerDict.pop(name, None)          # 造「新建」前置态
    app_logger = logging.getLogger(app_name)           # 快照内已存在的 logger
    root = logging.getLogger()
    ini = tmp_path / "logging_guard.ini"
    ini.write_text(_TEST_INI, encoding="utf-8")

    before_handlers = list(root.handlers)
    before_level = root.level
    before_app = (app_logger.disabled, app_logger.level, app_logger.propagate)
    captured = {}
    try:
        app_logger.disabled = False
        app_logger.setLevel(logging.NOTSET)
        app_logger.propagate = True
        assert name not in manager.loggerDict
        with migrate._alembic_logging_guard():
            logging.config.fileConfig(str(ini), disable_existing_loggers=False)
            captured["obj"] = logging.getLogger(name)
            assert name in manager.loggerDict, "前置：fileConfig 必须真的新建了该 logger"
            assert captured["obj"].handlers, "前置：该 logger 在迁移期拿到过 handler"
            # 顺带把快照内 logger 改成坏状态，验证还原逻辑没被新代码破坏
            app_logger.disabled = True
            app_logger.setLevel(logging.ERROR)
            app_logger.propagate = False

        assert name not in manager.loggerDict, "守卫退出后新建的 alembic logger 必须被摘除"
        assert captured["obj"].handlers == [], "摘除前必须关掉并清空它的 handler"
        assert app_logger.disabled is False, "快照内 logger 的 disabled 要还原"
        assert app_logger.level == logging.NOTSET, "快照内 logger 的 level 要还原"
        assert app_logger.propagate is before_app[2], "快照内 logger 的 propagate 要还原"
        assert list(root.handlers) == before_handlers
        assert root.level == before_level
    finally:
        if held is not None:
            manager.loggerDict[name] = held
        app_logger.disabled, app_logger.level, app_logger.propagate = before_app
