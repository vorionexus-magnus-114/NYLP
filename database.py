"""
Database module for Scholarship Finder.

Provides SQLite storage at data/scholarships.db relative to project root.
"""

import sqlite3
from pathlib import Path

# Project root is parent of src directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "scholarships.db"
# Kept for backward compatibility with older tests that patch this name.
# get_db_path() honours it, so patching either name can never silently fall
# through to the real database.
DATABASE_PATH = DB_PATH
_DEFAULT_DB_PATH = DB_PATH

# Expected schema for migration checks
EXPECTED_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "name": "TEXT",
    "provider": "TEXT",
    "description": "TEXT",
    "education_level": "TEXT",
    "qualification": "TEXT",
    "field_of_study": "TEXT",
    "minimum_age": "INTEGER",
    "maximum_age": "INTEGER",
    "nationality": "TEXT",
    "eligible_provinces": "TEXT",
    "eligible_cities": "TEXT",
    "eligible_domiciles": "TEXT",
    "minimum_marks": "REAL",
    "minimum_cgpa": "REAL",
    "household_income_limit": "REAL",
    "funding": "TEXT",
    "study_destination": "TEXT",
    "location": "TEXT",
    "deadline": "TEXT",
    "status": "TEXT",
    "application_url": "TEXT",
    "source_url": "TEXT",
    "verified_at": "TEXT",
    "raw_data": "TEXT",
    # Stable row identifier from the source workbook (e.g. "PKSF-0001"), used as
    # the strongest deduplication key. NULL when the workbook has none.
    "external_id": "TEXT",
}

CONNECT_TIMEOUT_SECONDS = 30


def get_db_path():
    """
    Return the database path to use.

    Both DB_PATH and the legacy DATABASE_PATH alias are honoured. If either has
    been overridden (e.g. by a test) that override wins. If they have been
    overridden to two different locations the call fails loudly instead of
    guessing, so a mis-patched test can never write to the real database.
    """
    db = Path(DB_PATH)
    legacy = Path(DATABASE_PATH)
    default = Path(_DEFAULT_DB_PATH)
    if db != default and legacy != default and db != legacy:
        raise RuntimeError(
            f"DB_PATH ({db}) and DATABASE_PATH ({legacy}) point to different locations; "
            "refusing to choose one."
        )
    if db != default:
        return db
    if legacy != default:
        return legacy
    return db


def get_connection():
    """
    Return a SQLite connection to the scholarship database.

    Automatically creates the parent directory for the configured DB path if needed.
    Respects DB_PATH override for tests without touching the real data directory.
    Caller is responsible for closing the connection.
    """
    db_path = get_db_path()
    # Ensure its parent directory exists - do not swallow errors silently
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f"Failed to create database directory {db_path.parent}: {e}") from e

    try:
        conn = sqlite3.connect(str(db_path), timeout=CONNECT_TIMEOUT_SECONDS)
    except Exception as e:
        raise RuntimeError(f"Failed to connect to database at {db_path}: {e}") from e

    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
    except Exception as e:
        conn.close()
        raise RuntimeError(f"Failed to configure database connection: {e}") from e

    return conn


def _ensure_schema(conn):
    """
    Create the table if needed and add any missing columns.

    Never drops or recreates the table, so existing rows are preserved. Must be
    called inside a transaction opened by the caller; DDL is transactional in
    SQLite, so a failure part-way through a migration rolls back cleanly.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scholarships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            provider TEXT,
            description TEXT,
            education_level TEXT,
            qualification TEXT,
            field_of_study TEXT,
            minimum_age INTEGER,
            maximum_age INTEGER,
            nationality TEXT,
            eligible_provinces TEXT,
            eligible_cities TEXT,
            eligible_domiciles TEXT,
            minimum_marks REAL,
            minimum_cgpa REAL,
            household_income_limit REAL,
            funding TEXT,
            study_destination TEXT,
            location TEXT,
            deadline TEXT,
            status TEXT,
            application_url TEXT,
            source_url TEXT,
            verified_at TEXT,
            raw_data TEXT,
            external_id TEXT
        );
        """
    )
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(scholarships);").fetchall()}
    except Exception as e:
        raise RuntimeError(f"Failed to verify database schema: {e}") from e

    if "id" not in existing:
        raise RuntimeError(
            "Existing 'scholarships' table has no 'id' column; refusing to migrate automatically. "
            "Existing data was not modified."
        )

    for col, col_type in EXPECTED_COLUMNS.items():
        if col == "id" or col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE scholarships ADD COLUMN {col} {col_type};")
        except Exception as e:
            raise RuntimeError(
                f"Failed to migrate missing column '{col}': {e}. "
                "The migration was rolled back and existing data was not modified."
            ) from e


_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_scholarships_name ON scholarships(name);",
    "CREATE INDEX IF NOT EXISTS idx_scholarships_deadline ON scholarships(deadline);",
    "CREATE INDEX IF NOT EXISTS idx_scholarships_status ON scholarships(status);",
    "CREATE INDEX IF NOT EXISTS idx_scholarships_provider ON scholarships(provider);",
    "CREATE INDEX IF NOT EXISTS idx_scholarships_education_level ON scholarships(education_level);",
    "CREATE INDEX IF NOT EXISTS idx_scholarships_external_id ON scholarships(external_id);",
)


def _ensure_verification_cache_table(conn):
    """Create verification_cache table if not exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_cache (
            scholarship_id INTEGER PRIMARY KEY,
            scholarship_name TEXT,
            provider TEXT,
            verified_at TEXT,
            source_url TEXT,
            status TEXT,
            claims_json TEXT,
            discrepancies_json TEXT,
            raw_evidence TEXT,
            FOREIGN KEY (scholarship_id) REFERENCES scholarships(id) ON DELETE CASCADE
        );
        """
    )


def seed_database_if_empty(workbook_path=None):
    """
    Check if scholarships table is empty, and if so, automatically import records
    from the default or specified Excel workbook.
    """
    if workbook_path is None:
        workbook_path = DATA_DIR / "Pakistan_Scholarship_Finder_Master.xlsx"

    p = Path(workbook_path)
    if not p.exists():
        return {"seeded": False, "reason": f"Workbook not found at {p}"}

    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM scholarships").fetchone()[0]
        if count > 0:
            return {"seeded": False, "count": count, "reason": "Database already contains records"}

        from src.data_import import import_workbook
        res = import_workbook(p)
        return {"seeded": True, "import_summary": res}
    finally:
        conn.close()


def init_db():
    """
    Initialize the scholarships database (idempotent).

    Schema creation, column migration and index creation run in ONE explicit
    transaction: either everything applies or nothing does. Existing data is
    never destroyed. Failures raise RuntimeError with an actionable message.
    """
    try:
        conn = get_connection()
    except Exception as e:
        raise RuntimeError(f"Database initialization failed: cannot get connection: {e}") from e

    try:
        # Explicit transaction so DDL/migration is atomic (the sqlite3 module's
        # default mode would not open one for DDL).
        conn.execute("BEGIN IMMEDIATE")
        _ensure_schema(conn)
        _ensure_verification_cache_table(conn)
        for stmt in _INDEX_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Database initialization failed: cannot create schema: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass
