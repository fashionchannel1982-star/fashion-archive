"""
Fashion Archive — Database Initialisation
Run once to create all tables.

Usage:
    cd backend
    source venv/bin/activate
    python scripts/init_db.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.database import engine, Base


async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✓ Database schema created.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
