from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk

from .main_window import MainWindow as BaseMainWindow, ComponentDialog, STATUS, money
from ..services import PcStorcError

MOVEMENTS = {
    "OPENING": "Начальный остаток",
    "RECEIPT": "Приход",
    "ADJUSTMENT": "Корректировка",
    "SALE": "Продажа",
}


class MultilineDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, prompt: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None
        self.geometry("720x430")

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=prompt).pack(anchor="w", pady=(0, 8))
        self.text = tk.Text(frame, wrap="word", font=("Consolas", 10))
        self.text.pack(fill="both", expand=True)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Продолжить", command=self._save).pack(side="right", padx=6)
        self.text.focus_set()

    def _save(self) -> None:
        value = self.text.get("1.0", "end-1c").strip()
        if not value:
            messagebox.showerror("PcStorc", "Вставьте хотя бы одну строку", parent=self)
            return
        self.result = value
        self.destroy()


class MainWindow(BaseMainWindow):
    """Исправленный интерфейс PcStorc."""

    def __init__(self, root, db) -> None:
        self.history_tree = None
        super().__init__(root, db)
        self.history_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.insert(2, self.history_tab, text="История")
        self._history_ui()
        self.refresh_history()

    def _build_ui(self) -> None:
        bar = ttk.Frame(self.build_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Сборки", style="Title.TLabel").pack(side="left")
        for text, command in [
            ("Новая сборка", self.new_build),
            ("Быстрый конфиг", self.quick_build),
            ("В резерв", self.reserve_build),
            ("Продать", self.sell),
            ("Отменить", self.cancel_build),
        ]:
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=3)

        cols = ("code", "status", "customer", "cost", "sale", "profit", "date")
        self.build_tree = ttk.Treeview(self.build_tab, columns=cols, show="headings")
        specs = [
            ("code", "Код", 150), ("status", "Статус", 100), ("customer", "Клиент", 180),
            ("cost", "Себестоимость", 120), ("sale", "Продажа", 120), ("profit", "Прибыль", 120),
            ("date", "Создано", 160),
        ]
        for column, title, width in specs:
            self.build_tree.heading(column, text=title)
            self.build_tree.column(column, width=width, anchor="w" if column == "customer" else "center")
        self.build_tree.pack(fill="both", expand=True)
        self.build_tree.bind("<Double-1>", lambda _event: self.show_build())

    def _history_ui(self) -> None:
        bar = ttk.Frame(self.history_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="История движений", style="Title.TLabel").pack(side="left")
        ttk.Button(bar, text="Обновить", command=self.refresh_history).pack(side="right")

        cols = ("date", "category", "model", "kind", "qty", "price", "note")
        self.history_tree = ttk.Treeview(self.history_tab, columns=cols, show="headings")
        specs = [
            ("date", "Дата", 160), ("category", "Категория", 150), ("model", "Модель", 260),
            ("kind", "Операция", 120), ("qty", "Кол-во", 80), ("price", "Цена", 110),
            ("note", "Примечание", 300),
        ]
        for column, title, width in specs:
            self.history_tree.heading(column, text=title)
            anchor = "center" if column in {"date", "kind", "qty", "price"} else "w"
            self.history_tree.column(column, width=width, anchor=anchor)
        self.history_tree.pack(fill="both", expand=True)

    def refresh(self) -> None:
        super().refresh()
        if self.history_tree is not None and self.history_tree.winfo_exists():
            self.refresh_history()

    def refresh_builds(self) -> None:
        self.build_tree.delete(*self.build_tree.get_children())
        for build in self.builds.list_builds():
            sale = money(build["sale_price"]) if build["sale_price"] else "—"
            profit = money(build["profit"]) if build["status"] == "SOLD" or build["sale_price"] else "—"
            self.build_tree.insert(
                "", "end", iid=str(build["id"]),
                values=(
                    build["code"], STATUS.get(build["status"], build["status"]), build["customer"],
                    money(build["cost_total"]), sale, profit, build["created_at"],
                ),
            )

    def refresh_history(self) -> None:
        if self.history_tree is None:
            return
        self.history_tree.delete(*self.history_tree.get_children())
        for movement in self.inventory.list_movements():
            self.history_tree.insert(
                "", "end",
                values=(
                    movement["created_at"], movement["category"], movement["model"],
                    MOVEMENTS.get(movement["kind"], movement["kind"]), movement["quantity"],
                    money(movement["unit_price"]), movement["note"],
                ),
            )

    def add_component(self) -> None:
        dialog = ComponentDialog(self.root)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        try:
            self.inventory.add_component(**dialog.result)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("PcStorc", str(exc))

    def quick_build(self) -> None:
        dialog = MultilineDialog(
            self.root,
            "Быстрый конфиг",
            "Вставьте конфигурацию: по одной комплектующей на строку",
        )
        self.root.wait_window(dialog)
        text = dialog.result
        if not text:
            return

        matched, unmatched, details = self.builds.quick_match_config(text)
        if not matched:
            messagebox.showwarning("PcStorc", "Не удалось сопоставить позиции со складом")
            return

        lines = [f"✓ {item['line']} → {item['component']['model']}" for item in details]
        if unmatched:
            lines.extend(["", "Не найдено:", *unmatched])
        lines.extend(["", "Создать резерв из найденных позиций?"])
        if messagebox.askyesno("PcStorc", "\n".join(lines)):
            try:
                self.builds.create_build(matched, description="Быстрый ввод", status="RESERVED")
                self.refresh()
            except PcStorcError as exc:
                messagebox.showerror("PcStorc", str(exc))

    def reserve_build(self) -> None:
        try:
            build = self.selected_build()
            self.builds.reserve_draft(build["id"])
            self.refresh()
        except PcStorcError as exc:
            messagebox.showerror("PcStorc", str(exc))

    def save_backup_settings(self) -> bool:
        backup_time = self.backup_time.get().strip()
        try:
            datetime.strptime(backup_time, "%H:%M")
        except ValueError:
            messagebox.showerror("PcStorc", "Время нужно в формате 21:00")
            return False

        folder = self.backup_folder.get().strip()
        if not folder:
            messagebox.showerror("PcStorc", "Выберите папку для резервных копий")
            return False

        self.db.set_setting("backup_folder", folder)
        self.db.set_setting("backup_time", backup_time)
        self.refresh_backup()
        return True

    def manual_backup(self) -> None:
        if not self.save_backup_settings():
            return
        try:
            path = self.backups.create_backup("manual")
            messagebox.showinfo("PcStorc", f"Копия создана:\n{path}")
            self.refresh_backup()
        except Exception as exc:
            messagebox.showerror("PcStorc", str(exc))
