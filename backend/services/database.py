"""
Fashion Archive — Database Service
Async SQLAlchemy database layer.
All DB operations go through here — never query directly from routers.
"""

import os
import uuid
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
from sqlalchemy import String, Integer, Float, DateTime, JSON, ForeignKey, Text, select, func
from sqlalchemy.dialects.postgresql import UUID

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/fashion_archive")
# SQLAlchemy async requires postgresql+asyncpg://
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    brand: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    youtube_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    video_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # Twelve Labs video_id
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)    # Twelve Labs task_id
    status: Mapped[str] = mapped_column(String, default="queued")
    looks_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    moments: Mapped[list["Moment"]] = relationship("Moment", back_populates="show", cascade="all, delete-orphan")
    provenance: Mapped[Optional["Provenance"]] = relationship("Provenance", back_populates="show", uselist=False)


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    show_id: Mapped[str] = mapped_column(String, ForeignKey("shows.id"), nullable=False)
    look_number: Mapped[int] = mapped_column(Integer, default=0)
    timestamp_start: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp_end: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enriched_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    show: Mapped["Show"] = relationship("Show", back_populates="moments")


class Provenance(Base):
    __tablename__ = "provenance"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    show_id: Mapped[str] = mapped_column(String, ForeignKey("shows.id"), nullable=False, unique=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    submitted_by: Mapped[str] = mapped_column(String, nullable=False)
    access_tier: Mapped[str] = mapped_column(String, default="public")
    usage_rights: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embargo_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attribution_display: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    restrictions_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    show: Mapped["Show"] = relationship("Show", back_populates="provenance")


# ─────────────────────────────────────────
# OPERATIONS
# ─────────────────────────────────────────

async def create_show(session: AsyncSession, data: dict) -> Show:
    show = Show(**data)
    session.add(show)
    await session.commit()
    await session.refresh(show)
    return show


async def get_show(session: AsyncSession, show_id: str) -> Optional[Show]:
    result = await session.execute(select(Show).where(Show.id == show_id))
    return result.scalar_one_or_none()


async def list_shows(session: AsyncSession, limit: int = 20, offset: int = 0) -> list[Show]:
    result = await session.execute(
        select(Show).order_by(Show.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def update_show_status(
    session: AsyncSession,
    show_id: str,
    status: str,
    video_id: str = None,
    looks_count: int = None,
    summary: str = None,
) -> None:
    show = await get_show(session, show_id)
    if show:
        show.status = status
        if video_id:
            show.video_id = video_id
        if looks_count is not None:
            show.looks_count = looks_count
        if summary:
            show.summary = summary
        await session.commit()


async def bulk_create_moments(session: AsyncSession, moments: list[dict]) -> None:
    for m in moments:
        moment = Moment(**m)
        session.add(moment)
    await session.commit()


# Keep backward compatibility — bulk_create_looks is an alias
async def bulk_create_looks(session: AsyncSession, looks: list[dict]) -> None:
    await bulk_create_moments(session, looks)


async def get_moment(session: AsyncSession, moment_id: str) -> Optional[Moment]:
    result = await session.execute(select(Moment).where(Moment.id == moment_id))
    return result.scalar_one_or_none()


async def create_schema(conn):
    """Create all tables. Called from init_db.py."""
    await conn.run_sync(Base.metadata.create_all)
