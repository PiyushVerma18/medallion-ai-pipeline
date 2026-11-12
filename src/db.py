from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from src.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _engine_for_stage_role(role_name: str):
    """Create a separate pool that assumes a restricted PostgreSQL role."""
    stage_engine = create_engine(DATABASE_URL, pool_pre_ping=True)

    @event.listens_for(stage_engine, "checkout")
    def assume_stage_role(dbapi_connection, _connection_record, _connection_proxy):
        # SET ROLE is session state. Reapply it on every pool checkout because a
        # prior failed transaction may have rolled back a role change.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET ROLE {role_name}")
            dbapi_connection.commit()
        finally:
            cursor.close()

    return stage_engine


silver_engine = _engine_for_stage_role("medallion_silver")
gold_engine = _engine_for_stage_role("medallion_gold")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_stage_roles() -> None:
    """Apply idempotent stage-role grants using the configured admin connection."""
    setup_sql = Path(__file__).resolve().parents[1] / "scripts" / "setup_stage_roles.sql"
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        try:
            cursor.execute(setup_sql.read_text(encoding="utf-8"))
            raw_connection.commit()
        finally:
            cursor.close()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
