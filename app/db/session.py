from __future__ import annotations

from sqlalchemy import Engine, create_engine as _create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine(database_url: str) -> Engine:
    return _create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
