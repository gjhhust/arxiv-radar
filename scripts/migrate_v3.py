"""
migrate_v3.py — Idempotent DB migration for arxiv-radar v3.0 schema.

Adds new columns to the `papers` table (if they don't already exist):
  - contribution_type TEXT  — incremental | significant | story-heavy | foundational
  - editorial_note    TEXT  — 1-2句编辑判断
  - why_read          TEXT  — 1句推荐理由

Safe to run multiple times (uses PRAGMA table_info to check existence).
"""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

# Add scripts dir to path so we can import PaperDB
sys.path.insert(0, str(Path(__file__).parent))
from paper_db import DEFAULT_DB_PATH


NEW_COLUMNS = [
    ("contribution_type", "TEXT"),
    ("editorial_note",    "TEXT"),
    ("why_read",          "TEXT"),
]


def migrate(db_path: Path | None = None) -> None:
    """
    Idempotently add v3.0 columns to the papers table.

    Args:
        db_path: path to SQLite DB; defaults to DEFAULT_DB_PATH from paper_db.py
    """
    db_path = db_path or DEFAULT_DB_PATH

    if not db_path.exists():
        print(f"DB not found at {db_path}, creating a fresh one via PaperDB init...")
        from paper_db import PaperDB
        PaperDB(db_path)  # initialises schema

    conn = sqlite3.connect(str(db_path))
    try:
        # Get existing columns
        existing = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}

        added = []
        for col_name, col_type in NEW_COLUMNS:
            if col_name in existing:
                print(f"  ✓ Column '{col_name}' already exists — skipping")
            else:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {col_type}")
                added.append(col_name)
                print(f"  + Added column '{col_name}' ({col_type})")

        conn.commit()

        if added:
            print(f"\nMigration done. New columns: {', '.join(added)}")
        else:
            print("\nMigration done. New columns: contribution_type, editorial_note, why_read")
            print("(all columns already present — nothing changed)")

    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
