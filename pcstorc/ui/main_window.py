from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..backup import BackupManager
from ..database import Database
from ..services import BuildItemInput, BuildService, DEFAULT_CATEGORIES, InventoryService, PcStorcError

STATUS_LABELS = {
    "DRAFT": "Ð§ÐµÑ€Ð½Ð¾Ð²Ð¸Ðº",
    "RESERVED": "Ð’ Ñ€ÐµÐ·ÐµÑ€Ð²Ðµ",
    "SOLD": "ÐŸÑ€Ð¾Ð´Ð°Ð½Ð¾",
    "CANCELED": "ÐžÑ‚Ð¼ÐµÐ½ÐµÐ½Ð¾",
}

STOCK_LABELS = {"GREEN": "Ð—ÐµÐ»ÐµÐ½Ñ‹Ð¹", "YELLOW": "Ð–ÐµÐ»Ñ‚Ñ‹Ð¹", "RED": "ÐšÑ€Ð°ÑÐ½Ñ‹Ð¹"}


def money(value: float | int | None) -> str:
    try:
        return f"{float(value or 0):,.0f} â‚½".replace(",", " ")
    except (TypeError, ValueError):
        return "0 â‚½"


def parse_float(text: str, field: str = "Ð—Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ") -> float:
    text = text.strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise PcStorcError(f"{field}: Ð²Ð²ÐµÐ´Ð¸Ñ‚Ðµ Ñ‡Ð¸ÑÐ»Ð¾") from exc


def parse_int(text: str, field: str = "Ð—Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ") -> int:
    text = text.strip().replace(" ", "")
    try:
        return int(text)
    except ValueError as exc:
        raise PcStorcError(f"{field}: Ð²Ð²ÐµÐ´Ð¸Ñ‚Ðµ Ñ†ÐµÐ»Ð¾Ðµ Ñ‡Ð¸ÑÐ»Ð¾") from exc


class MainWindow:
    def __init__(self, root: tk.Tk, db: Database) -> None:
        self.root = root
        self.db = db
        self.inventory = InventoryService(db)
        self.builds = BuildService(db, self.inventory)
        self.backups = BackupManager(db)

        self.root.title("PcStorc â€” ÑƒÑ‡ÐµÑ‚ ÐºÐ¾Ð¼Ð¿Ð»ÐµÐºÑ‚ÑƒÑŽÑ‰Ð¸Ñ…")
        self.root.geometry("1420x860")
        self.root.minsize(1120, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._configure_style()
        self._build_ui()
        self.refresh_all()
        self._schedule_backup_check()

    def _configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("CardTitle.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Metric.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(10, 6))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text="PcStorc", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  Ð¡ÐºÐ»Ð°Ð´ â€¢ Ñ€ÐµÐ·ÐµÑ€Ð²Ñ‹ â€¢ ÑÐ±Ð¾Ñ€ÐºÐ¸ â€¢ Ð¿Ñ€Ð¾Ð´Ð°Ð¶Ð¸", foreground="#666").pack(side="left", pady=(7, 0))
        self.header_status = ttk.Label(header, text="")
        self.header_status.pack(side="right", pady=(7, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        self.inventory_tab = ttk.Frame(self.notebook, padding=12)
        self.builds_tab = ttk.Frame(self.notebook, padding=12)
        self.movements_tab = ttk.Frame(self.notebook, padding=12)
        self.backup_tab = ttk.Frame(self.notebook, padding=12)

        self.notebook.add(self.dashboard_tab, text="ÐžÐ±Ð·Ð¾Ñ€")
        self.notebook.add(self.inventory_tab, text="Ð¡ÐºÐ»Ð°Ð´")
        self.notebook.add(self.builds_tab, text="Ð¡Ð±Ð¾Ñ€ÐºÐ¸ Ð¸ Ð¿Ñ€Ð¾Ð´Ð°Ð¶Ð¸")
        self.notebook.add(self.movements_tab, text="Ð”Ð²Ð¸Ð¶ÐµÐ½Ð¸Ñ")
        self.notebook.add(self.backup_tab, text="Ð ÐµÐ·ÐµÑ€Ð²Ð½Ñ‹Ðµ ÐºÐ¾Ð¿Ð¸Ð¸")

        self._build_dashboard_tab()
        self._build_inventory_tab()
        self._build_builds_tab()
        self._build_movements_tab()
        self._build_backup_tab()

    # ---------- Dashboard ----------
    def _build_dashboard_tab(self) -> None:
        metrics = ttk.Frame(self.dashboard_tab)
        metrics.pack(fill="x", pady=(0, 12))
        self.metric_labels: dict[str, ttk.Label] = {}
        for key, title in [
            ("positions", "ÐŸÐ¾Ð·Ð¸Ñ†Ð¸Ð¹ Ð½Ð° ÑÐºÐ»Ð°Ð´Ðµ"),
            ("reserved", "Ð’ Ñ€ÐµÐ·ÐµÑ€Ð²Ðµ, ÑˆÑ‚."),
            ("red", "ÐšÑ€Ð°ÑÐ½Ð°Ñ Ð·Ð¾Ð½Ð°"),
            ("sales", "ÐŸÑ€Ð¾Ð´Ð°Ð¶"),
            ("profit", "ÐŸÑ€Ð¸Ð±Ñ‹Ð»ÑŒ Ð²ÑÐµÐ³Ð¾"),
        ]:
            card = ttk.LabelFrame(metrics, text=title, padding=14)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            label = ttk.Label(card, text="0", style="Metric.TLabel")
            label.pack(anchor="w")
            self.metric_labels[key] = label

        ttk.Label(self.dashboard_tab, text="Ð§Ñ‚Ð¾ Ð½ÑƒÐ¶Ð½Ð¾ ÐºÐ¾Ð½Ñ‚Ñ€Ð¾Ð»Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 6))
        columns = ("status", "category", "model", "available", "delivery", "supplier", "action")
        self.alert_tree = ttk.Treeview(self.dashboard_tab, columns=columns, show="headings", height=17)
        for col, text, width in [
            ("status", "Ð£Ñ€Ð¾Ð²ÐµÐ½ÑŒ", 95),
            ("category", "ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ", 170),
            ("model", "ÐœÐ¾Ð´ÐµÐ»ÑŒ", 350),
            ("available", "Ð”Ð¾ÑÑ‚ÑƒÐ¿Ð½Ð¾", 90),
            ("delivery", "Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ°", 90),
            ("supplier", "ÐŸÐ¾ÑÑ‚Ð°Ð²Ñ‰Ð¸Ðº", 160),
            ("action", "Ð ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð°Ñ†Ð¸Ñ", 160),
        ]:
            self.alert_tree.heading(col, text=text)
            self.alert_tree.column(col, width=width, anchor="center" if col in {"status", "available", "delivery"} else "w")
        self.alert_tree.tag_configure("RED", background="#ffd9d9")
        self.alert_tree.tag_configure("YELLOW", background="#fff2bf")
        self.alert_tree.pack(fill="both", expand=True)

    # ---------- Inventory ----------
    def _build_inventory_tab(self) -> None:
        bar = ttk.Frame(self.inventory_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="+ ÐÐ¾Ð²Ð°Ñ Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ", command=self.add_component).pack(side="left")
        ttk.Button(bar, text="ÐŸÑ€Ð¸Ñ…Ð¾Ð´", command=self.receive_stock).pack(side="left", padx=4)
        ttk.Button(bar, text="Ð ÐµÐ´Ð°ÐºÑ‚Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ", command=self.edit_component).pack(side="left", padx=4)
        ttk.Button(bar, text="ÐšÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ Ð¾ÑÑ‚Ð°Ñ‚Ð¾Ðº", command=self.adjust_stock).pack(side="left", padx=4)
        ttk.Button(bar, text="ÐžÐ±Ð½Ð¾Ð²Ð¸Ñ‚ÑŒ", command=self.refresh_all).pack(side="left", padx=4)
        ttk.Label(bar, text="ÐŸÐ¾Ð¸ÑÐº:").pack(side="left", padx=(18, 4))
        self.inventory_search = tk.StringVar()
        search = ttk.Entry(bar, textvariable=self.inventory_search, width=35)
        search.pack(side="left")
        self.inventory_search.trace_add("write", lambda *_: self.refresh_inventory())

        columns = (
            "category", "model", "quantity", "reserved", "available", "status",
            "price", "supplier", "delivery", "thresholds", "urgency",
        )
        self.inventory_tree = ttk.Treeview(self.inventory_tab, columns=columns, show="headings")
        settings = [
            ("category", "ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ", 170),
            ("model", "ÐœÐ¾Ð´ÐµÐ»ÑŒ", 310),
            ("quantity", "ÐÐ°Ð»Ð¸Ñ‡Ð¸Ðµ", 75),
            ("reserved", "Ð ÐµÐ·ÐµÑ€Ð²", ²È="25½¹•¹Ñ}‰½à€ôÑÑ¬¹½µ‰½‰½à¡˜°Ñ•áÑÙ…É¥…‰±”õÍ•±˜¹½µÁ½¹•¹Ð°ÍÑ…Ñ”ô‰É•…‘½¹±äˆ°Ý¥‘Ñ ôØÀ¤ìÍ•±˜¹½µÁ½¹•¹Ñ}‰½à¹É¥¡É½ÜôÄ°½±Õµ¸ôÄ°Á…‘àôà°Á…‘äôÐ¤(€€€€€€€Í•±˜¹½µÁ½¹•¹Ñ}‰½à¹‰¥¹ ˆðñ½µ‰½‰½áM•±•Ñ•øøˆ°±…µ‰‘„}”èÍ•±˜¹}±½…‘}ÁÉ¥” ¤¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐô‹BkBûBïBãFB×FFBËBøˆ¤¹É¥¡É½ÜôÈ°½±Õµ¸ôÀ°ÍÑ¥­äô‰Üˆ°Á…‘äôÐ¤ìÑÑ¬¹¹ÑÉä¡˜°Ñ•áÑÙ…É¥…‰±”õÍ•±˜¹ÅÑä¤¹É¥¡É½ÜôÈ°½±Õµ¸ôÄ°ÍÑ¥­äô‰Üˆ°Á…‘àôà°Á…‘äôÐ¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐô‹B‡B×BÇB×FFBûBãBóBûFFF0ƒBßBÀƒF#F¸ˆ¤¹É¥¡É½ÜôÌ°½±Õµ¸ôÀ°ÍÑ¥­äô‰Üˆ°Á…‘äôÐ¤ìÑÑ¬¹¹ÑÉä¡˜°Ñ•áÑÙ…É¥…‰±”õÍ•±˜¹½ÍÐ¤¹É¥¡É½ÜôÌ°½±Õµ¸ôÄ°ÍÑ¥­äô‰Üˆ°Á…‘àôà°Á…‘äôÐ¤(€€€€€€€Í•±˜¹ÍÑ½­}±…‰•°€ôÑÑ¬¹1…‰•°¡˜°Ñ•áÐôˆˆ°™½É•É½Õ¹ôˆŒØØØˆ¤ìÍ•±˜¹ÍÑ½­}±…‰•°¹É¥¡É½ÜôÐ°½±Õµ¸ôÀ°½±Õµ¹ÍÁ…¸ôÈ°ÍÑ¥­äô‰Üˆ°Á…‘äôÐ¤(€€€€€€€ÑÑ¬¹	ÕÑÑ½¸¡˜°Ñ•áÐô‹BSBûBÇBÃBËBãFF0ˆ°½µµ…¹õÍ•±˜¹…‘¤¹É¥¡É½ÜôÔ°½±Õµ¸ôÀ°½±Õµ¹ÍÁ…¸ôÈ°Á…‘äôà¤(€€€€€€€Í•±˜¹}±½…‘}½µÁ½¹•¹ÑÌ ¤((€€€‘•˜}™¥±Ñ•É•¡Í•±˜¤€´ø±¥ÍÑm‘¥Ñtè(€€€€€€€É•ÑÕÉ¸mŒ™½ÈŒ¥¸Í•±˜¹½µÁ½¹•¹ÑÌ¥˜l‰…Ñ•½Éä‰t€ôôÍ•±˜¹…Ñ•½Éä¹•Ð ¥t((€€€‘•˜}±½…‘}½µÁ½¹•¹ÑÌ¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥Ñ•µÌ€ôÍ•±˜¹}™¥±Ñ•É• ¤(€€€€€€€Ù…±Õ•Ì€ôml‰µ½‘•°‰t™½ÈŒ¥¸¥Ñ•µÍt(€€€€€€€Í•±˜¹½µÁ½¹•¹Ñ}‰½ál‰Ù…±Õ•Ì‰t€ôÙ…±Õ•Ì(€€€€€€€¥˜Ù…±Õ•Ìè(€€€€€€€€€€€Í•±˜¹½µÁ½¹•¹Ð¹Í•Ð¡Ù…±Õ•ÍlÁt¤ìÍ•±˜¹}±½…‘}ÁÉ¥” ¤(€€€€€€€•±Í”è(€€€€€€€€€€€Í•±˜¹½µÁ½¹•¹Ð¹Í•Ð ˆˆ¤((€€€‘•˜}Í•±•Ñ•¡Í•±˜¤€´ø‘¥Ðð9½¹”è(€€€€€€€É•ÑÕÉ¸¹•áÐ ¡Œ™½ÈŒ¥¸Í•±˜¹}™¥±Ñ•É• ¤¥˜l‰µ½‘•°‰t€ôôÍ•±˜¹½µÁ½¹•¹Ð¹•Ð ¤¤°9½¹”¤((€€€‘•˜}±½…‘}ÁÉ¥”¡Í•±˜¤€´ø9½¹”è(€€€€€€€Œ€ôÍ•±˜¹}Í•±•Ñ• ¤(€€€€€€€¥˜Œè(€€€€€€€€€€€Í•±˜¹½ÍÐ¹Í•Ð¡ÍÑÈ¡l‰±…ÍÑ}ÁÕÉ¡…Í•}ÁÉ¥”‰t¤¤(€€€€€€€€€€€Í•±˜¹ÍÑ½­}±…‰•°¹½¹™¥ÕÉ”¡Ñ•áÐõ˜‹B‡BËBûBÇBûBÓB÷BøƒFB×BçFBÃFèíl…Ù…¥±…‰±”uôƒF#F¸€£BÈƒB÷BÃBïBãFBãBàílÅÕ…¹Ñ¥Ñäuô°ƒBÈƒFB×BßB×FBËBÔílÉ•Í•ÉÙ•uô¤ˆ¤((€€€‘•˜…‘¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÑÉäè(€€€€€€€€€€€Œ€ôÍ•±˜¹}Í•±•Ñ• ¤(€€€€€€€€€€€¥˜¹½ÐŒèÉ…¥Í”AMÑ½ÉÉÉ½È ‹BKF/BÇB×FBãFBÔƒBëBûBóBÿBïB×BëFFF;F'FF8ˆ¤(€€€€€€€€€€€ÅÑä€ôÁ…ÉÍ•}¥¹Ð¡Í•±˜¹ÅÑä¹•Ð ¤°€‹BkBûBïBãFB×FFBËBøˆ¤ì½ÍÐ€ôÁ…ÉÍ•}™±½…Ð¡Í•±˜¹½ÍÐ¹•Ð ¤°€‹B‡B×BÇB×FFBûBãBóBûFFF0ˆ¤(€€€€€€€€€€€¥˜ÅÑä€ðô€ÀèÉ…¥Í”AMÑ½ÉÉÉ½È ‹BkBûBïBãFB×FFBËBøƒBÓBûBïBÛB÷BøƒBÇF/FF0ƒBÇBûBïF3F#BÔƒB÷FBïF<ˆ¤(€€€€€€€€€€€Í•±˜¹½¹}…‘¡	Õ¥±‘%Ñ•µ%¹ÁÕÐ¡l‰¥‰t°ÅÑä°½ÍÐ¤¤ìÍ•±˜¹‘•ÍÑÉ½ä ¤(€€€€€€€•á•ÁÐAMÑ½ÉÉÉ½È…Ì•áŒè(€€€€€€€€€€€µ•ÍÍ…•‰½à¹Í¡½Ý•ÉÉ½È ‰AMÑ½ÉŒˆ°ÍÑÈ¡•áŒ¤°Á…É•¹ÐõÍ•±˜¤(()±…ÍÌEÕ¥­½¹™¥¥…±½œ¡Ñ¬¹Q½Á±•Ù•°¤è(€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°Á…É•¹Ð°‰Õ¥±‘Ìè	Õ¥±‘M•ÉÙ¥”°½¹}…•ÁÐ¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡Á…É•¹Ð¤(€€€€€€€Í•±˜¹‰Õ¥±‘Ì°Í•±˜¹½¹}…•ÁÐ€ô‰Õ¥±‘Ì°½¹}…•ÁÐ(€€€€€€€Í•±˜¹µ…Ñ¡•Ìè±¥ÍÑm	Õ¥±‘%Ñ•µ%¹ÁÕÑt€ômt(€€€€€€€Í•±˜¹Ñ¥Ñ±” ‹BGF/FFFF/BäƒBËBËBûBÐƒBëBûB÷FBãBÏFFBÃFBãBàˆ¤(€€€€€€€Í•±˜¹•½µ•ÑÉä ˆÜØÁàØÀÀˆ¤(€€€€€€€Í•±˜¹ÑÉ…¹Í¥•¹Ð¡Á…É•¹Ð¤ìÍ•±˜¹É…‰}Í•Ð ¤(€€€€€€€˜€ôÑÑ¬¹É…µ”¡Í•±˜°Á…‘‘¥¹œôÄÈ¤ì˜¹Á…¬¡™¥±°ô‰‰½Ñ ˆ°•áÁ…¹õQÉÕ”¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐô‹BKFFBÃBËF3FBÔƒBëBûB÷FBãBÏFFBÃFBãF8ƒŠPƒBÿBøƒBûBÓB÷BûBäƒBëBûBóBÿBïB×BëFFF;F'B×BäƒBÈƒFFFBûBëBÔ¸AMÑ½ÉŒƒBÿBûBÿF/FBÃB×FFF<ƒFBûBÿBûFFBÃBËBãFF0ƒB×BÔƒFƒBóBûBÓB×BïF?BóBàƒB÷BÀƒFBëBïBÃBÓBÔ¸ˆ°ÝÉ…Á±•¹Ñ ôÜÀÀ¤¹Á…¬¡…¹¡½Èô‰Üˆ¤(€€€€€€€Í•±˜¹Ñ•áÐ€ôÑ¬¹Q•áÐ¡˜°¡•¥¡ÐôÄÐ°ÝÉ…Àô‰Ý½Éˆ¤ìÍ•±˜¹Ñ•áÐ¹Á…¬¡™¥±°ô‰‰½Ñ ˆ°•áÁ…¹õQÉÕ”°Á…‘äôà¤(€€€€€€€ÑÑ¬¹	ÕÑÑ½¸¡˜°Ñ•áÐô‹BƒBÃFBÿBûBßB÷BÃFF0ˆ°½µµ…¹õÍ•±˜¹µ…Ñ ¤¹Á…¬¡…¹¡½Èô‰Üˆ¤(€€€€€€€Í•±˜¹É•ÍÕ±Ð€ôÑ¬¹Q•áÐ¡˜°¡•¥¡ÐôÄÀ°ÝÉ…Àô‰Ý½Éˆ°ÍÑ…Ñ”ô‰‘¥Í…‰±•ˆ¤ìÍ•±˜¹É•ÍÕ±Ð¹Á…¬¡™¥±°ô‰‰½Ñ ˆ°•áÁ…¹õQÉÕ”°Á…‘äôà¤(€€€€€€€ÑÑ¬¹	ÕÑÑ½¸¡˜°Ñ•áÐô‹BSBûBÇBÃBËBãFF0ƒFBÃFBÿBûBßB÷BÃB÷B÷F/BÔƒBÿBûBßBãFBãBàˆ°½µµ…¹õÍ•±˜¹…•ÁÐ¤¹Á…¬¡…¹¡½Èô‰”ˆ¤((€€€‘•˜µ…Ñ ¡Í•±˜¤€´ø9½¹”è(€€€€€€€Í•±˜¹µ…Ñ¡•Ì°Õ¹µ…Ñ¡•°‘•Ñ…¥±Ì€ôÍ•±˜¹‰Õ¥±‘Ì¹ÅÕ¥­}µ…Ñ¡}½¹™¥œ¡Í•±˜¹Ñ•áÐ¹•Ð ˆÄ¸Àˆ°€‰•¹ˆ¤¤(€€€€€€€±¥¹•Ì€ômt(€€€€€€€™½È¥¸‘•Ñ…¥±Ìè(€€€€€€€€€€€Œ€ô‘l‰½µÁ½¹•¹Ð‰t(€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡˜‹ŠrLí‘l±¥¹”uô€ƒŠH€íl…Ñ•½Éäuôèílµ½‘•°uô€¡í‘lÍ½É”tè¸À•ô¤ˆ¤(€€€€€€€™½È±¥¹”¥¸Õ¹µ…Ñ¡•è(€€€€€€€€€€€±¥¹•Ì¹…ÁÁ•¹¡˜‹Šr\ƒBwBÔƒB÷BÃBçBÓB×B÷Bøèí±¥¹•ôˆ¤(€€€€€€€Í•±˜¹É•ÍÕ±Ð¹½¹™¥ÕÉ”¡ÍÑ…Ñ”ô‰¹½Éµ…°ˆ¤ìÍ•±˜¹É•ÍÕ±Ð¹‘•±•Ñ” ˆÄ¸Àˆ°€‰•¹ˆ¤ìÍ•±˜¹É•ÍÕ±Ð¹¥¹Í•ÉÐ ˆÄ¸Àˆ°€‰q¸ˆ¹©½¥¸¡±¥¹•Ì¤½È€‹BwB×FB×BÏBøƒFBÃFBÿBûBßB÷BÃBËBÃFF0ˆ¤ìÍ•±˜¹É•ÍÕ±Ð¹½¹™¥ÕÉ”¡ÍÑ…Ñ”ô‰‘¥Í…‰±•ˆ¤((€€€‘•˜…•ÁÐ¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹µ…Ñ¡•Ìè(€€€€€€€€€€€Í•±˜¹µ…Ñ  ¤(€€€€€€€¥˜Í•±˜¹µ…Ñ¡•Ìè(€€€€€€€€€€€Í•±˜¹½¹}…•ÁÐ¡Í•±˜¹µ…Ñ¡•Ì¤ìÍ•±˜¹‘•ÍÑÉ½ä ¤(()±…ÍÌM•±±¥…±½œ¡Ñ¬¹Q½Á±•Ù•°¤è(€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°Á…É•¹Ð°‰Õ¥±è‘¥Ð°Í•ÉÙ¥”è	Õ¥±‘M•ÉÙ¥”°½¹}Í…Ù•õ9½¹”¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡Á…É•¹Ð¤(€€€€€€€Í•±˜¹‰Õ¥±°Í•±˜¹Í•ÉÙ¥”°Í•±˜¹½¹}Í…Ù•€ô‰Õ¥±°Í•ÉÙ¥”°½¹}Í…Ù•(€€€€€€€Í•±˜¹Ñ¥Ñ±”¡˜‹BFBûBÓBÃBÛBÀí‰Õ¥±‘l½‘”uôˆ¤(€€€€€€€Í•±˜¹ÑÉ…¹Í¥•¹Ð¡Á…É•¹Ð¤ìÍ•±˜¹É…‰}Í•Ð ¤ìÍ•±˜¹É•Í¥é…‰±”¡…±Í”°…±Í”¤(€€€€€€€˜€ôÑÑ¬¹É…µ”¡Í•±˜°Á…‘‘¥¹œôÄÐ¤ì˜¹Á…¬ ¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐõ˜‹B‡B×BÇB×FFBûBãBóBûFFF0èíµ½¹•ä¡‰Õ¥±‘l½ÍÑ}Ñ½Ñ…°t¥ôˆ°ÍÑå±”ô‰…É‘Q¥Ñ±”¹Q1…‰•°ˆ¤¹Á…¬¡…¹¡½Èô‰Üˆ¤(€€€€€€€Í•±˜¹Í…±”€ôÑ¬¹MÑÉ¥¹Y…È¡Ù…±Õ”õÍÑÈ¡‰Õ¥±‘l‰Í…±•}ÁÉ¥”‰t½È€À¤¤(€€€€€€€É½Ü€ôÑÑ¬¹É…µ”¡˜¤ìÉ½Ü¹Á…¬¡™¥±°ô‰àˆ°Á…‘äôÄÀ¤(€€€€€€€ÑÑ¬¹1…‰•°¡É½Ü°Ñ•áÐô‹B›B×B÷BÀƒBÿFBûBÓBÃBÛBàèˆ¤¹Á…¬¡Í¥‘”ô‰±•™Ðˆ¤(€€€€€€€ÑÑ¬¹¹ÑÉä¡É½Ü°Ñ•áÑÙ…É¥…‰±”õÍ•±˜¹Í…±”°Ý¥‘Ñ ôÈÀ¤¹Á…¬¡Í¥‘”ô‰±•™Ðˆ°Á…‘àôà¤(€€€€€€€ÑÑ¬¹	ÕÑÑ½¸¡˜°Ñ•áÐô‹BBûBÓFBËB×FBÓBãFF0ƒBÿFBûBÓBÃBÛFƒBàƒFBÿBãFBÃFF0ƒFBøƒFBëBïBÃBÓBÀˆ°½µµ…¹õÍ•±˜¹Í…Ù”¤¹Á…¬¡…¹¡½Èô‰”ˆ¤((€€€‘•˜Í…Ù”¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÑÉäè(€€€€€€€€€€€ÁÉ¥”€ôÁ…ÉÍ•}™±½…Ð¡Í•±˜¹Í…±”¹•Ð ¤°€‹B›B×B÷BÀƒBÿFBûBÓBÃBÛBàˆ¤(€€€€€€€€€€€ÁÉ½™¥Ð€ôÁÉ¥”€´™±½…Ð¡Í•±˜¹‰Õ¥±‘l‰½ÍÑ}Ñ½Ñ…°‰t¤(€€€€€€€€€€€¥˜¹½Ðµ•ÍÍ…•‰½à¹…Í­å•Í¹¼ ‰AMÑ½ÉŒˆ°˜‹B‡BÿBãFBÃFF0ƒBëBûBóBÿBïB×BëFFF;F'BãBÔƒFBøƒFBëBïBÃBÓBÀýq»BFBûBÓBÃBÛBÀèíµ½¹•ä¡ÁÉ¥”¥õq»BFBãBÇF/BïF0èíµ½¹•ä¡ÁÉ½™¥Ð¥ôˆ°Á…É•¹ÐõÍ•±˜¤èÉ•ÑÕÉ¸(€€€€€€€€€€€Í•±˜¹Í•ÉÙ¥”¹Í•±±}‰Õ¥±¡Í•±˜¹‰Õ¥±‘l‰¥‰t°ÁÉ¥”¤(€€€€€€€€€€€¥˜Í•±˜¹½¹}Í…Ù•èÍ•±˜¹½¹}Í…Ù• ¤(€€€€€€€€€€€Í•±˜¹‘•ÍÑÉ½ä ¤(€€€€€€€•á•ÁÐAMÑ½ÉÉÉ½È…Ì•áŒè(€€€€€€€€€€€µ•ÍÍ…•‰½à¹Í¡½Ý•ÉÉ½È ‰AMÑ½ÉŒˆ°ÍÑÈ¡•áŒ¤°Á…É•¹ÐõÍ•±˜¤(()±…ÍÌ	Õ¥±‘Y¥•Ý¥…±½œ¡Ñ¬¹Q½Á±•Ù•°¤è(€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°Á…É•¹Ð°‰Õ¥±è‘¥Ð¤€´ø9½¹”è(€€€€€€€ÍÕÁ•È ¤¹}}¥¹¥Ñ}|¡Á…É•¹Ð¤(€€€€€€€Í•±˜¹Ñ¥Ñ±”¡‰Õ¥±‘l‰½‘”‰t¤(€€€€€€€Í•±˜¹•½µ•ÑÉä ˆàÈÁàÔÈÀˆ¤(€€€€€€€Í•±˜¹ÑÉ…¹Í¥•¹Ð¡Á…É•¹Ð¤(€€€€€€€˜€ôÑÑ¬¹É…µ”¡Í•±˜°Á…‘‘¥¹œôÄÐ¤ì˜¹Á…¬¡™¥±°ô‰‰½Ñ ˆ°•áÁ…¹õQÉÕ”¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐõ˜‰í‰Õ¥±‘l½‘”uôƒŠPíMQQUM}1	1L¹•Ð¡‰Õ¥±‘lÍÑ…ÑÕÌt°‰Õ¥±‘lÍÑ…ÑÕÌt¥ôˆ°ÍÑå±”ô‰Q¥Ñ±”¹Q1…‰•°ˆ¤¹Á…¬¡…¹¡½Èô‰Üˆ¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐõ˜‹BkBïBãB×B÷Fèí‰Õ¥±‘lÕÍÑ½µ•Èt½È€ŸŠPô€€€ƒB{BÿBãFBÃB÷BãBÔèí‰Õ¥±‘l‘•ÍÉ¥ÁÑ¥½¸t½È€ŸŠPôˆ¤¹Á…¬¡…¹¡½Èô‰Üˆ°Á…‘äô À°€à¤¤(€€€€€€€ÑÉ•”€ôÑÑ¬¹QÉ••Ù¥•Ü¡˜°½±Õµ¹Ìô ‰…Ñ•½Éäˆ°€‰µ½‘•°ˆ°€‰ÅÑäˆ°€‰½ÍÐˆ°€‰ÍÕ´ˆ¤°Í¡½Üô‰¡•…‘¥¹Ìˆ¤(€€€€€€€™½È½°°Ñ•áÐ°Ý¥‘Ñ ¥¸l ‰…Ñ•½Éäˆ°€‹BkBÃFB×BÏBûFBãF<ˆ°€ÄÜÀ¤°€ ‰µ½‘•°ˆ°€‹BsBûBÓB×BïF0ˆ°€ÌÌÀ¤°€ ‰ÅÑäˆ°€‹BkBûBì·BËBøˆ°€ÜÀ¤°€ ‰½ÍÐˆ°€‹B›B×B÷BÀˆ°€ÄÀÀ¤°€ ‰ÍÕ´ˆ°€‹B‡FBóBóBÀˆ°€ÄÄÀ¥tè(€€€€€€€€€€€ÑÉ•”¹¡•…‘¥¹œ¡½°°Ñ•áÐõÑ•áÐ¤ìÑÉ•”¹½±Õµ¸¡½°°Ý¥‘Ñ õÝ¥‘Ñ °…¹¡½Èô‰•¹Ñ•Èˆ¥˜½°¥¸ì‰ÅÑäˆ°€‰½ÍÐˆ°€‰ÍÕ´‰ô•±Í”€‰Üˆ¤(€€€€€€€™½È¥Ñ•´¥¸‰Õ¥±‘l‰¥Ñ•µÌ‰tè(€€€€€€€€€€€ÑÉ•”¹¥¹Í•ÉÐ ˆˆ°€‰•¹ˆ°Ù…±Õ•Ìô¡¥Ñ•µl‰…Ñ•½Éå}Í¹…ÁÍ¡½Ð‰t°¥Ñ•µl‰½µÁ½¹•¹Ñ}¹…µ•}Í¹…ÁÍ¡½Ð‰t°¥Ñ•µl‰ÅÕ…¹Ñ¥Ñä‰t°µ½¹•ä¡¥Ñ•µl‰Õ¹¥Ñ}½ÍÐ‰t¤°µ½¹•ä¡¥Ñ•µl‰ÅÕ…¹Ñ¥Ñä‰t€¨¥Ñ•µl‰Õ¹¥Ñ}½ÍÐ‰t¤¤¤(€€€€€€€ÑÉ•”¹Á…¬¡™¥±°ô‰‰½Ñ ˆ°•áÁ…¹õQÉÕ”¤(€€€€€€€ÑÑ¬¹1…‰•°¡˜°Ñ•áÐõ˜‹B‡B×BÇB×FFBûBãBóBûFFF0èíµ½¹•ä¡‰Õ¥±‘l½ÍÑ}Ñ½Ñ…°t¥ô€€€ƒBFBûBÓBÃBÛBÀèíµ½¹•ä¡‰Õ¥±‘lÍ…±•}ÁÉ¥”t¥ô€€€ƒBFBãBÇF/BïF0èíµ½¹•ä¡‰Õ¥±‘lÁÉ½™¥Ðt¥ôˆ°ÍÑå±”ô‰…É‘Q¥Ñ±”¹Q1…‰•°ˆ¤¹Á…¬¡…¹¡½Èô‰”ˆ°Á…‘äôÄÀ¤(()‘•˜}Í…™•}±½Ý•È¡Ù…±Õ”èÍÑÈ¤€´øÍÑÈè(€€€É•ÑÕÉ¸€¡Ù…±Õ”½È€ˆˆ¤¹±½Ý•È ¤¹É•Á±…” ‹FDˆ°€‹BÔˆ¤(