"""
Fashion Archive — Database initialisation
Run once: python backend/scripts/init_db.py
"""

import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/fashion_archive")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS shows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand TEXT NOT NULL,
  season TEXT NOT NULL,
  year INTEGER NOT NULL,
  twelve_labs_video_id TEXT,
  summary TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS moments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  show_id UUID REFERENCES shows(id) ON DELETE CASCADE,
  timestamp_start FLOAT NOT NULL,
  timestamp_end FLOAT NOT NULL,
  description TEXT,
  thumbnail_url TEXT,
  embedding VECTOR(1024),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS moments_show_id_idx ON moments(show_id);
CREATE INDEX IF NOT EXISTS moments_embedding_idx ON moments
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
"""

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(SCHEMA)
        print("✓ Database schema created.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
