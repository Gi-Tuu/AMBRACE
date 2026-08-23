"""数据库初始化脚本（用于手动重建表）"""
import asyncio
from app.db.database import init_db


async def main():
    print("正在初始化数据库...")
    await init_db()
    print("数据库表创建完成！")


if __name__ == "__main__":
    asyncio.run(main())
