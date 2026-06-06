"""
Database layer with SQLAlchemy ORM for Universal Social Assistant.
(Файл ранее содержал markdown-обёртку — из-за неё ломался импорт и авто-discovery tools.)
"""
import logging
import os
from pathlib import Path

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, create_engine
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)


def _resolve_database_url() -> str:
    """DATABASE_PATH может быть полным URL или путём к файлу SQLite (как в .env без sqlite:///)."""
    raw = (os.getenv("DATABASE_PATH") or "").strip()
    if not raw:
        return "sqlite:///./data/database.sqlite"
    if "://" in raw:
        return raw
    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    return str(URL.create("sqlite", database=str(path)))


DATABASE_URL = _resolve_database_url()
logger.info("Database URL: %s", DATABASE_URL)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Database initialization failed: %s", e)
        raise


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True)
    name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    role = Column(String, nullable=False, default="child")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    parents = relationship("Parent", back_populates="user", cascade="all, delete-orphan")
    children = relationship("Child", back_populates="user", cascade="all, delete-orphan")
    group_memberships = relationship("GroupMember", back_populates="user", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="user", cascade="all, delete-orphan")
    progress = relationship("Progress", back_populates="user", cascade="all, delete-orphan")
    digital_twin = relationship("DigitalTwin", back_populates="user", cascade="all, delete-orphan")
    psychology = relationship("Psychology", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("Settings", back_populates="user", cascade="all, delete-orphan")


class Parent(Base):
    __tablename__ = "parents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    __table_args__ = (Index("idx_parent_user_id", "user_id"),)
    user = relationship("User", back_populates="parents")


class Child(Base):
    __tablename__ = "children"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    __table_args__ = (Index("idx_child_user_id", "user_id"),)
    user = relationship("User", back_populates="children")


class ParentChildLink(Base):
    __tablename__ = "parent_child_links"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("parents.id"), nullable=False)
    child_id = Column(Integer, ForeignKey("children.id"), nullable=False)
    __table_args__ = (
        Index("idx_parent_child_parent_id", "parent_id"),
        Index("idx_parent_child_child_id", "child_id"),
    )
    parent = relationship("Parent")
    child = relationship("Child")


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    group_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    group_memberships = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_in_group = Column(String, nullable=False)
    __table_args__ = (
        Index("idx_group_member_group_id", "group_id"),
        Index("idx_group_member_user_id", "user_id"),
    )
    group = relationship("Group", back_populates="group_memberships")
    user = relationship("User", back_populates="group_memberships")


class Schedule(Base):
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    datetime_start = Column(DateTime, nullable=False)
    datetime_end = Column(DateTime, nullable=False)
    schedule_type = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (
        Index("idx_schedule_user_id", "user_id"),
        Index("idx_schedule_group_id", "group_id"),
        Index("idx_schedule_start_time", "datetime_start"),
        Index("idx_schedule_end_time", "datetime_end"),
    )
    user = relationship("User", back_populates="schedules")
    group = relationship("Group", back_populates="schedules")


class Progress(Base):
    __tablename__ = "progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject = Column(String, nullable=False)
    level = Column(Integer)
    last_update = Column(DateTime, default=func.now())
    notes = Column(Text, nullable=True)
    __table_args__ = (
        Index("idx_progress_user_id", "user_id"),
        Index("idx_progress_subject", "subject"),
    )
    user = relationship("User", back_populates="progress")


class DigitalTwin(Base):
    __tablename__ = "digital_twin"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    json_profile = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_digital_twin_user_id", "user_id"),)
    user = relationship("User", back_populates="digital_twin")


class Psychology(Base):
    __tablename__ = "psychology"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    json_state = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_psychology_user_id", "user_id"),)
    user = relationship("User", back_populates="psychology")


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    json_settings = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    __table_args__ = (Index("idx_settings_user_id", "user_id"),)
    user = relationship("User", back_populates="settings")
