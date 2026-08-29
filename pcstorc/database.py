from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

APP_NAME = "PcStorc"
DB_FILENAME = "pcstorc.db"


def default_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def default_db_path() -> Path:
    return default_data_dir() / DB_FILENAME


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._initialize()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _initialize(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                model TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                last_purchase_price REAL NOT NULL DEFAULT 0 CHECK(last_purchase_price >= 0),
                supplier TEXT NOT NULL DEFAULT '',
                delivery_days INTEGER NOT NULL DEFAULT 1 CHECK(delivery_days >= 0),
                yellow_level INTEGER NOT NULL DEFAULT 2 CHECK(yellow_level >= 0),
                red_level INTEGER NOT NULL DEFAULT 1 CHECK(red_level >= 0),
                notes TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category, model)
            );

            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                customer TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'RESERVED'
                    CHECK(status IN ('DRAFT','RESERVED','SOLD','CANCELED')),
                sale_price REAL NOT NULL DEFAULT 0 CHECK(sale_price >= 0),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reserved_at TEXT,
                sold_at TEXT,
                canceled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS build_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                build_id INTEGER NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
                component_id INTEGER NOT NULL REFERENCES components(id),
                quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
                unit_cost REAL NOT NULL DEFAULT 0 CHECK(unit_cost >= 0),
                category_snapshot TEXT NOT NULL,
                component_name_snapshot TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_id INTEGER NOT NULL REFERENCES components(id),
                kind TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL DEFAULT 0 CHECK(unit_price >= 0),
                reference_type TEXT NOT NULL DEFAULT '',
                reference_id INTEGER,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS backup_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_builds_status ON builds(status);
            CREATE INDEX IF NOT EXISTS idx_build_items_build ON build_items(build_id);
            CREATE INDEX IF NOT EXISTS idx_build_items_component ON build_items(component_id);
            CREATE INDEX IF NOT EXISTS idx_movements_component ON movements(component_id);
            CREATE INDEX IF NOT EXISTS idx_movements_created ON movements(created_at DESC);
            """
        )
        defaults = {
            "backup_time": "21:00",
            "backup_retention": "30",
            "backup_on_close": "1",
            "backup_folder": str(self.default_backup_dir()),
        }
        with self.transaction() as conn:
            for key, value in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (key, value),
                )

    def default_backup_dir(self) -> Path:
        if os.name == "nt":
            documents = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
        else:
            documents = Path.home() / "Documents"
        return documents / "PcStorc Backups"

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def checkpoint(self) -> None:
        self._conn.execute("PRAGMA wal_checkpoint(FULL)")
