import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import database  # noqa: E402
from src.database import get_connection  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Redirect every database call to a throw-away SQLite file."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.init_db()
    return db_path


def add_scholarship(record, raw=None):
    """Insert one scholarship row and return its id."""
    payload = dict(record)
    if raw is not None:
        payload["raw_data"] = json.dumps(raw)
    columns = list(payload)
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"INSERT INTO scholarships ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            [payload[c] for c in columns],
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


@pytest.fixture()
def make_scholarship():
    return add_scholarship
