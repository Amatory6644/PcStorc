from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from pcstorc.backup import BackupManager
from pcstorc.database import Database, default_data_dir


def configure_logging() -> None:
    log_dir = default_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "pcstorc.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )


def run_backup_only() -> int:
    db = Database()
    try:
        path = BackupManager(db).create_backup("daily")
        logging.info("Daily backup created: %s", path)
        return 0
    except Exception:
        logging.exception("Daily backup failed")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass


def run_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox
    from pcstorc.ui.main_window import MainWindow

    db = Database()
    root = tk.Tk()
    try:
        MainWindow(root, db)
        root.mainloop()
        return 0
    except Exception as exc:
        logging.exception("Fatal UI error")
        try:
            messagebox.showerror("PcStorc", f"Критическая ошибка:\n{exc}")
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        return 1


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="PcStorc — учет комплектующих для сборки ПК")
    parser.add_argument("--backup", action="store_true", help="Создать резервную копию и завершиться")
    args = parser.parse_args()
    if args.backup:
        return run_backup_only()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
