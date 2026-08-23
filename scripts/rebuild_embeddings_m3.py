# -*- coding: utf-8 -*-
# 权威重建向量库：以 SQLite memories 表为准，逐条用 bge-m3(1024d) 重算嵌入重建 Chroma collection。
import asyncio, sqlite3, sys
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "backend"))
import chromadb
from chromadb.config import Settings
from app.config import settings
from app.memory.embedding import text_embedding
from app.db.vector_store import COLLECTION_NAME

DB = _root / "backend" / "data" / "sqlite" / "ai_companion.db"
BATCH = 200


async def rebuild():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("select id, character_id, memory_type, content, importance from memories").fetchall()
    conn.close()
    print("sqlite memories:", len(rows))
    client = chromadb.Client(Settings(persist_directory=settings.chroma_persist_dir, anonymized_telemetry=False, is_persistent=True))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    newcol = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        ids = [str(r["id"]) for r in chunk]
        docs = [r["content"] for r in chunk]
        metas = [{"memory_id": r["id"], "character_id": r["character_id"],
                  "memory_type": r["memory_type"], "importance": int(r["importance"] or 0)} for r in chunk]
        embs = [await text_embedding(d) for d in docs]
        newcol.add(ids=ids, embeddings=embs, documents=docs, metadatas=metas)
        done += len(chunk)
        print("embedded", done, "/", len(rows))
    print("done, collection count:", newcol.count())
    e = newcol.get(include=["embeddings"], limit=1)
    print("dim:", e["embeddings"][0].shape if e["embeddings"] else "none")


if __name__ == "__main__":
    asyncio.run(rebuild())