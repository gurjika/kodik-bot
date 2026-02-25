"""
SQLAlchemy async database layer.

Models:
  Message     — every user message + AI response
  Escalation  — ask_human escalations & admin replies

Usage:
  from storage.database import init_db, get_session, Message, Escalation

  # At startup:
  await init_db()

  # In request handlers:
  async with get_session() as session:
      session.add(Message(...))
      await session.commit()
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import String, BigInteger, Text, DateTime, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass



class Message(Base):
    """Stores every user↔bot exchange."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    user_text: Mapped[str] = mapped_column(Text)
    ai_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Escalation(Base):
    """Tracks ask_human escalations and admin replies."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    user_chat_id: Mapped[int] = mapped_column(BigInteger)
    question: Mapped[str] = mapped_column(Text)
    admin_msg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        SAEnum("pending", "resolved", name="escalation_status"),
        default="pending",
    )
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )



async def init_db() -> None:
    """Create engine, session factory, and all tables (if they don't exist)."""
    global _engine, _session_factory

    settings = get_settings()
    _engine = create_async_engine(settings.DATABASE_URL, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized (%s)", settings.DATABASE_URL)


def get_session() -> AsyncSession:
    """Return a new async session. Use as `async with get_session() as s:`."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _session_factory()
