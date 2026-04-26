from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

DATABASE_URL = "sqlite:///./elite_scheduler.db"

Base = declarative_base()

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String)
    api_id = Column(Integer)
    api_hash = Column(String)
    cycle_delay_min = Column(Integer, default=20)
    msg_delay_sec = Column(Integer, default=300)
    use_copy = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, nullable=True)

    groups = relationship("Group", back_populates="owner", cascade="all, delete-orphan")
    stats = relationship("Stats", back_populates="account", uselist=False, cascade="all, delete-orphan")

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"))

    owner = relationship("Account", back_populates="groups")

class Stats(Base):
    __tablename__ = "stats"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), unique=True)
    success_total = Column(Integer, default=0)
    fail_total = Column(Integer, default=0)
    current_cycle_success = Column(Integer, default=0)
    current_cycle_fail = Column(Integer, default=0)
    status = Column(String, default="Idle")
    next_msg_at = Column(DateTime, nullable=True)
    last_cycle_at = Column(DateTime, nullable=True)

    account = relationship("Account", back_populates="stats")

class GlobalConfig(Base):
    __tablename__ = "global_config"
    key = Column(String, primary_key=True)
    value = Column(Text)

from sqlalchemy import create_engine
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
