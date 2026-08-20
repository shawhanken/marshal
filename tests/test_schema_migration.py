from sqlalchemy import create_engine, inspect, text
from marshal_core.knowledge.models import ensure_schema


def test_ensure_schema_adds_missing_introduced_at_ts(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    # simulate a pre-existing DB whose escape_registry predates the new column
    with eng.begin() as c:
        c.execute(text("CREATE TABLE escape_registry "
                       "(id VARCHAR PRIMARY KEY, status VARCHAR)"))
    ensure_schema(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("escape_registry")}
    assert "introduced_at_ts" in cols


def test_ensure_schema_fresh_db_has_column(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    ensure_schema(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("escape_registry")}
    assert "introduced_at_ts" in cols
