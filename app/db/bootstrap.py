"""Apply the reviewed contract schema and the engine role grants to a PostgreSQL database."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, text

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "schema.sql"
GRANTS = Path(__file__).resolve().parent / "grants.sql"


def apply_contract_v1(engine: Engine) -> None:
    """Run only as an explicit deployment/bootstrap action, never on app startup."""
    with engine.begin() as connection:
        connection.connection.execute(SCHEMA.read_text(encoding="utf-8"))


def apply_grants(engine: Engine, engine_password: Optional[str] = None) -> None:
    """Create/refresh the read-only htn_engine role. Idempotent."""
    with engine.begin() as connection:
        connection.connection.execute(GRANTS.read_text(encoding="utf-8"))
        if engine_password:
            connection.execute(text("SELECT set_config('htn.pw', :pw, true)"), {"pw": engine_password})
            connection.execute(text("DO $$ BEGIN EXECUTE format('ALTER ROLE htn_engine PASSWORD %L', "
                                    "current_setting('htn.pw')); END $$"))


def init_database(engine: Engine, engine_password: Optional[str] = None) -> dict[str, bool]:
    """Schema if the database is empty, then grants. Safe to run repeatedly."""
    with engine.connect() as connection:
        has_schema = connection.execute(text("SELECT to_regclass('public.fact') IS NOT NULL")).scalar()
    if not has_schema:
        apply_contract_v1(engine)
    apply_grants(engine, engine_password)
    return {"schema_created": not has_schema, "grants_applied": True}
