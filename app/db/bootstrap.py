"""Apply the reviewed v1 contract schema to an empty PostgreSQL database."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine


def apply_contract_v1(engine: Engine) -> None:
    """Run only as an explicit deployment/bootstrap action, never on app startup."""
    schema = Path(__file__).resolve().parents[2] / "contracts" / "schema.sql"
    with engine.begin() as connection:
        connection.connection.execute(schema.read_text(encoding="utf-8"))
