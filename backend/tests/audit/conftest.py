import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest


async def _cancel_pending():
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return bool(pending)


def _draining_run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            pending = False
            try:
                pending = loop.run_until_complete(_cancel_pending())
            except Exception:
                pass
            if pending:
                loop.run_until_complete(asyncio.sleep(0.05))
                loop.run_until_complete(asyncio.sleep(0))
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                pass
    finally:
        asyncio.set_event_loop(None)
        loop.close()


# The audit-only runner skips the parent conftest and owns its own sandbox.
# During normal whole-suite collection the parent already initialized its sandbox;
# changing process globals here would leak into unrelated tests.
_STANDALONE = bool(os.environ.get("AUDIT_ROOT") and os.environ.get("AMBRACE_BACKEND"))
SANDBOX = None
if _STANDALONE:
    asyncio.run = _draining_run
    ROOT = Path(os.environ["AUDIT_ROOT"])
    SANDBOX = Path(tempfile.mkdtemp(prefix="session_", dir=ROOT / "tmp"))
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (SANDBOX / "empty.sqlite").as_posix()
    os.environ["CHROMA_PERSIST_DIR"] = str(SANDBOX / "vector")
    os.environ["AUTH_SECRET_KEY"] = "synthetic-only-0123456789abcdef0123456789abcdef"
    os.environ["AMBRACE_CREDENTIAL_KEY_FILE"] = str(SANDBOX / "synthetic.key")


@pytest.fixture(scope="session", autouse=True)
def _sandbox_schema():
    if _STANDALONE:
        from app.db.database import init_db
        asyncio.run(init_db())
    yield


@pytest.fixture(autouse=True)
def isolation(monkeypatch, tmp_path):
    from app.memory import bm25_index as bm, extract_queue as q
    from app.flags.agent_flags import AGENT_FLAGS
    from app.events.bus import event_bus
    import app.agent.llm_client as llm
    import app.memory.embedding as emb
    import app.memory.service as svc
    async def forbidden(*args, **kwargs):
        raise AssertionError('Network model invocation is forbidden in this audit')
    async def vector(text):
        return [1.0, 0.0]
    monkeypatch.setattr(llm, 'chat_completion', forbidden)
    monkeypatch.setattr(emb, 'text_embedding', vector)
    monkeypatch.setattr(svc, 'text_embedding', vector)
    monkeypatch.setattr(q, '_QUEUE_FILE', tmp_path / 'queue.json')
    monkeypatch.setattr(bm, '_persist_root', tmp_path / 'bm25')
    old_flags = dict(AGENT_FLAGS)
    old_subscribers = dict(event_bus._subscribers)
    bm._cache.clear()
    yield
    bm._cache.clear()
    AGENT_FLAGS.clear()
    AGENT_FLAGS.update(old_flags)
    event_bus._subscribers = old_subscribers


def pytest_sessionfinish(session, exitstatus):
    if _STANDALONE:
        from app.db.database import engine
        asyncio.run(engine.dispose())
        shutil.rmtree(SANDBOX, ignore_errors=True)
