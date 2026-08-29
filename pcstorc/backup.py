from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .database import Database


class BackupManager:
    def __init__(self, db: Database) -> None:
        self.db = db

    def backup_folder(self) -> Path:
        path = Path(self.db.get_setting("backup_folder", str(self.db.default_backup_dir()))).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def retention(self) -> int:
        try:
            return max(1, int(self.db.get_setting("backup_retention", "30")))
        except ValueError:
            return 30

    def create_backup(self, kind: str = "manual") -> Path:
        folder = self.backup_folder()
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch in "_- ").strip().replace(" ", "_") or "backup"
        target = folder / f"PcStorc_backup_{stamp}_{safe_kind}.zip"

        with tempfile.TemporaryDirectory(prefix="pcstorc-backup-") as tmp:
            tmpdir = Path(tmp)
            db_copy = tmpdir / "pcstorc.db"

            src = self.db.connection
            dest = sqlite3.connect(db_copy)
            try:
                src.backup(dest)
            finally:
                dest.close()

            self._export_csv(tmpdir)
            metadata = {
                "app": "PcStorc",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "kind": kind,
                "database_file": "pcstorc.db",
            }
            (tmpdir / "backup_info.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(tmpdir.iterdir()):
                    zf.write(path, arcname=path.name)

        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO backup_log(kind, path) VALUES(?, ?)",
                (kind, str(target)),
            )
        self.cleanup_old_backups()
        return target

    def _export_csv(self, folder: Path) -> None:
        exports = {
            "inventory.csv": """
                SELECT c.category AS Категория, c.model AS Модель, c.quantity AS В_наличии,
                       COALESCE(r.reserved,0) AS В_резерве,
                       c.quantity-COALESCE(r.reserved,0) AS Доступно,
                       c.last_purchase_price AS Последняя_закупочная_цена,
                       c.supplier AS Поставщик, c.delivery_days AS Доставка_дней,
                       c.yellow_level AS Желтый_порог, c.red_level AS Красный_порог,
                       c.notes AS Примечание
                FROM components c
                LEFT JOIN (
                    SELECT bi.component_id, SUM(bi.quantity) AS reserved
                    FROM build_items bi JOIN builds b ON b.id=bi.build_id
                    WHERE b.status='RESERVED'
                    GROUP BY bi.component_id
                ) r ON r.component_id=c.id
                WHERE c.active=1
                ORDER BY c.category, c.model
            """,
            "builds.csv": """
                SELECT b.code AS Код, b.customer AS Клиент, b.description AS Описание,
                       b.status AS Статус,
                       COALESCE(SUM(bi.quantity*bi.unit_cost),0) AS Себестоимость,
                       b.sale_price AS Цена_продажи,
                       b.sale_price-COALESCE(SUM(bi.quantity*bi.unit_cost),0) AS Прибыль,
                       b.created_at AS Создано, b.reserved_at AS Зарезервировано,
                       b.sold_at AS Продано, b.notes AS Примечание
                FROM builds b LEFT JOIN build_items bi ON bi.build_id=b.id
                GROUP BY b.id ORDER BY b.id DESC
            """,
            "build_items.csv": """
                SELECT b.code AS Сборка, bi.category_snapshot AS Категория,
                       bi.component_name_snapshot AS Модель, bi.quantity AS Количество,
                       bi.unit_cost AS Цена_в_себестоимости,
                       bi.quantity*bi.unit_cost AS Сумма
                FROM build_items bi JOIN builds b ON b.id=bi.build_id
                ORDER BY b.id DESC, bi.id
            """,
            "movements.csv": """
                SELECT m.created_at AS Дата, c.category AS Категория, c.model AS Модель,
                       m.kind AS Операция, m.quantity AS Количество, m.unit_price AS Цена,
                       m.reference_type AS Тип_ссылки, m.reference_id AS ID_ссылки,
                       m.note AS Примечание
                FROM movements m JOIN components c ON c.id=m.component_id
                ORDER BY m.id DESC
            """,
        }
        for filename, query in exports.items():
            rows = self.db.connection.execute(query).fetchall()
            path = folder / filename
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh, delimiter=";")
                if rows:
                    writer.writerow(rows[0].keys())
                    for row in rows:
                        writer.writerow(list(row))

    def cleanup_old_backups(self) -> None:
        backups = sorted(
            self.backup_folder().glob("PcStorc_backup_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[self.retention() :]:
            try:
                old.unlink()
            except OSError:
                pass

    def latest_backup(self) -> Path | None:
        backups = sorted(
            self.backup_folder().glob("PcStorc_backup_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return backups[0] if backups else None

    def has_daily_backup_today(self) -> bool:
        prefix = datetime.now().strftime("PcStorc_backup_%Y-%m-%d_")
        return any("_daily.zip" in p.name for p in self.backup_folder().glob(f"{prefix}*.zip"))

    @staticmethod
    def validate_backup(path: str | Path) -> None:
        path = Path(path)
        if not path.exists() or not zipfile.is_zipfile(path):
            raise ValueError("Файл не является резервной копией PcStorc")
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "pcstorc.db" not in names:
                raise ValueError("В резервной копии нет pcstorc.db")

    def restore_backup(self, path: str | Path) -> None:
        """Restore database bytes. Caller must close and reopen Database afterwards."""
        path = Path(path)
        self.validate_backup(path)
        self.db.checkpoint()
        self.db.close()
        with tempfile.TemporaryDirectory(prefix="pcstorc-restore-") as tmp:
            with zipfile.ZipFile(path, "r") as zf:
                zf.extract("pcstorc.db", path=tmp)
            restored = Path(tmp) / "pcstorc.db"
            self.db.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(restored, self.db.path)
            for suffix in ("-wal", "-shm"):
                side = Path(str(self.db.path) + suffix)
                if side.exists():
                    side.unlink()
