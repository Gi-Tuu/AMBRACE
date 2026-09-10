"""数据库初始化脚本（用于手动重建表）"""
import asyncio
import sys
from pathlib import Path

# 2026-08-24（新机部署报告 P1-4）：直接运行需把 backend/ 加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db.database import init_db


async def main():
    print("正在初始化数据库...")
    await init_db()
    print("数据库表创建完成！")


if __name__ == "__main__":
    asyncio.run(main())
