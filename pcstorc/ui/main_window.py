from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..backup import BackupManager
from ..database import Database
from ..services import BuildItemInput, BuildService, DEFAULT_CATEGORIES, InventoryService, PcStorcError

STATUS = {"DRAFT": "Черновик", "RESERVED": "В резерве", "SOLD": "Продано", "CANCELED": "Отменено"}
STOCK = {"GREEN": "Зеленый", "YELLOW": "Желтый", "RED": "Красный"}


def money(value) -> str:
    return f"{float(value or 0):,.0f} ₽".replace(",", " ")


def number(text: str, integer: bool = False):
    text = (text or "0").strip().replace(" ", "").replace(",", ".")
    return int(text) if integer else float(text)


class MainWindow:
    def __init__(self, root: tk.Tk, db: Database) -> None:
        self.root, self.db = root, db
        self.inventory = InventoryService(db)
        self.builds = BuildService(db, self.inventory)
        self.backups = BackupManager(db)
        root.title("PcStorc — учет комплектующих")
        root.geometry("1280x760")
        root.minsize(1000, 620)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._style()
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)
        self.stock_tab, self.build_tab, self.backup_tab = (ttk.Frame(self.tabs, padding=10) for _ in range(3))
        self.tabs.add(self.stock_tab, text="Склад")
        self.tabs.add(self.build_tab, text="Сборки и продажи")
        self.tabs.add(self.backup_tab, text="Резервные копии")
        self._stock_ui(); self._build_ui(); self._backup_ui(); self.refresh()
        root.after(60_000, self._daily_check)

    def _style(self):
        s = ttk.Style()
        try: s.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError: pass
        s.configure("Treeview", rowheight=28)
        s.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        s.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))

    def _stock_ui(self):
        bar = ttk.Frame(self.stock_tab); bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Склад", style="Title.TLabel").pack(side="left")
        for text, cmd in [("+ Позиция", self.add_component), ("Приход", self.receive), ("Корректировать остаток", self.adjust)]:
            ttk.Button(bar, text=text, command=cmd).pack(side="right", padx=3)
        cols = ("cat", "model", "qty", "reserved", "free", "status", "price", "supplier", "days", "need")
        self.stock_tree = ttk.Treeview(self.stock_tab, columns=cols, show="headings")
        specs = [("cat","Категория",150),("model","Модель",280),("qty","Наличие",70),("reserved","Резерв",70),
                 ("free","Свободно",75),("status","Уровень",85),("price","Закупка",100),("supplier","Поставщик",130),
                 ("days","Доставка",75),("need","Рекомендация",135)]
        for c,t,w in specs: self.stock_tree.heading(c,text=t); self.stock_tree.column(c,width=w,anchor="center" if c not in {"cat","model","supplier","need"} else "w")
        self.stock_tree.tag_configure("RED", background="#ffdada"); self.stock_tree.tag_configure("YELLOW", background="#fff1bd")
        self.stock_tree.pack(fill="both", expand=True)

    def _build_ui(self):
        bar = ttk.Frame(self.build_tab); bar.pack(fill="x", pady=(0,8))
        ttk.Label(bar, text="Сборки", style="Title.TLabel").pack(side="left")
        for text, cmd in [("Новая сборка", self.new_build), ("Быстрый конфиг", self.quick_build), ("Продать", self.sell), ("Отменить", self.cancel_build)]:
            ttk.Button(bar, text=text, command=cmd).pack(side="right", padx=3)
        cols=("code","status","customer","cost","sale","profit","date")
        self.build_tree=ttk.Treeview(self.build_tab,columns=cols,show="headings")
        specs=[("code","Код",150),("status","Статус",100),("customer","Клиент",180),("cost","Себестоимость",120),("sale","Продажа",120),("profit","Прибыль",120),("date","Создано",160)]
        for c,t,w in specs: self.build_tree.heading(c,text=t); self.build_tree.column(c,width=w,anchor="center" if c not in {"customer"} else "w")
        self.build_tree.pack(fill="both",expand=True); self.build_tree.bind("<Double-1>", lambda _e:self.show_build())

    def _backup_ui(self):
        ttk.Label(self.backup_tab,text="Резервные копии",style="Title.TLabel").pack(anchor="w")
        f=ttk.LabelFrame(self.backup_tab,text="Настройки",padding=12); f.pack(fill="x",pady=10)
        self.backup_folder=tk.StringVar(value=self.db.get_setting("backup_folder",str(self.db.default_backup_dir())))
        self.backup_time=tk.StringVar(value=self.db.get_setting("backup_time","21:00"))
        ttk.Label(f,text="Папка:").grid(row=0,column=0,sticky="w"); ttk.Entry(f,textvariable=self.backup_folder,width=80).grid(row=0,column=1,sticky="ew",padx=8)
        ttk.Button(f,text="Выбрать",command=self.choose_backup_folder).grid(row=0,column=2)
        ttk.Label(f,text="Ежедневно:").grid(row=1,column=0,sticky="w",pady=8); ttk.Entry(f,textvariable=self.backup_time,width=10).grid(row=1,column=1,sticky="w",padx=8)
        f.columnconfigure(1,weight=1)
        buttons=ttk.Frame(self.backup_tab); buttons.pack(fill="x")
        ttk.Button(buttons,text="Сохранить настройки",command=self.save_backup_settings).pack(side="left")
        ttk.Button(buttons,text="Создать копию сейчас",command=self.manual_backup).pack(side="left",padx=5)
        ttk.Button(buttons,text="Восстановить из ZIP",command=self.restore_backup).pack(side="left",padx=5)
        ttk.Button(buttons,text="Установить задачу Windows",command=self.install_task).pack(side="left",padx=5)
        self.backup_info=ttk.Label(self.backup_tab,text=""); self.backup_info.pack(anchor="w",pady=18)

    def refresh(self):
        self.refresh_stock(); self.refresh_builds(); self.refresh_backup()

    def refresh_stock(self):
        self.stock_tree.delete(*self.stock_tree.get_children())
        for x in self.inventory.list_components():
            self.stock_tree.insert("","end",iid=str(x["id"]),tags=(x["stock_status"],),values=(x["category"],x["model"],x["quantity"],x["reserved"],x["available"],STOCK[x["stock_status"]],money(x["last_purchase_price"]),x["supplier"],x["delivery_days"],x["urgency"]))

    def refresh_builds(self):
        self.build_tree.delete(*self.build_tree.get_children())
        for b in self.builds.list_builds():
            self.build_tree.insert("","end",iid=str(b["id"]),values=(b["code"],STATUS.get(b["status"],b["status"]),b["customer"],money(b["cost_total"]),money(b["sale_price"]),money(b["profit"]),b["created_at"]))

    def refresh_backup(self):
        p=self.backups.latest_backup()
        self.backup_info.config(text=f"Последняя копия: {p.name if p else 'еще не создана'}\nПапка: {self.backups.backup_folder()}")

    def selected_component(self):
        s=self.stock_tree.selection()
        if not s: raise PcStorcError("Выберите позицию на складе")
        return self.inventory.get_component(int(s[0]))

    def selected_build(self):
        s=self.build_tree.selection()
        if not s: raise PcStorcError("Выберите сборку")
        return self.builds.get_build(int(s[0]))

    def add_component(self):
        d=ComponentDialog(self.root)
        if not d.result: return
        try:
            self.inventory.add_component(**d.result); self.refresh()
        except Exception as e: messagebox.showerror("PcStorc",str(e))

    def receive(self):
        try: c=self.selected_component()
        except PcStorcError as e: return messagebox.showerror("PcStorc",str(e))
        qty=simpledialog.askinteger("Приход",f"{c['model']}\nКоличество:",minvalue=1,parent=self.root)
        if qty is None:return
        price=simpledialog.askfloat("Приход","Цена закупки за штуку:",initialvalue=c["last_purchase_price"],minvalue=0,parent=self.root)
        if price is None:return
        supplier=simpledialog.askstring("Приход","Поставщик:",initialvalue=c["supplier"],parent=self.root) or ""
        self.inventory.receive_stock(c["id"],qty,price,supplier); self.refresh()

    def adjust(self):
        try:c=self.selected_component()
        except PcStorcError as e:return messagebox.showerror("PcStorc",str(e))
        qty=simpledialog.askinteger("Остаток",f"Новый физический остаток {c['model']}:",initialvalue=c["quantity"],minvalue=0,parent=self.root)
        if qty is None:return
        try:self.inventory.adjust_stock(c["id"],qty,"Инвентаризация"); self.refresh()
        except PcStorcError as e:messagebox.showerror("PcStorc",str(e))

    def new_build(self):
        BuildDialog(self.root,self.inventory,self.builds,self.refresh)

    def quick_build(self):
        text=simpledialog.askstring("Быстрый конфиг","Вставьте комплектующие через перенос строки:",parent=self.root)
        if not text:return
        matched,unmatched,details=self.builds.quick_match_config(text)
        if not matched:return messagebox.showwarning("PcStorc","Не удалось сопоставить позиции со складом")
        msg="\n".join(f"✓ {d['line']} → {d['component']['model']}" for d in details)
        if unmatched:msg += "\n\nНе найдено:\n"+"\n".join(unmatched)
        if messagebox.askyesno("PcStorc",msg+"\n\nСоздать резерв?"):
            try:self.builds.create_build(matched,description="Быстрый ввод",status="RESERVED"); self.refresh()
            except PcStorcError as e:messagebox.showerror("PcStorc",str(e))

    def sell(self):
        try:b=self.selected_build()
        except PcStorcError as e:return messagebox.showerror("PcStorc",str(e))
        price=simpledialog.askfloat("Продажа",f"Себестоимость: {money(b['cost_total'])}\nЦена продажи:",initialvalue=b["sale_price"] or b["cost_total"],minvalue=0,parent=self.root)
        if price is None:return
        try:self.builds.sell_build(b["id"],price); messagebox.showinfo("PcStorc",f"Продано. Прибыль: {money(price-b['cost_total'])}"); self.refresh()
        except PcStorcError as e:messagebox.showerror("PcStorc",str(e))

    def cancel_build(self):
        try:b=self.selected_build(); self.builds.cancel_build(b["id"]); self.refresh()
        except PcStorcError as e:messagebox.showerror("PcStorc",str(e))

    def show_build(self):
        try:b=self.selected_build()
        except PcStorcError:return
        lines=[f"{b['code']} — {STATUS.get(b['status'],b['status'])}",f"Клиент: {b['customer'] or '—'}",""]
        lines += [f"{i['category_snapshot']}: {i['component_name_snapshot']} ×{i['quantity']} — {money(i['quantity']*i['unit_cost'])}" for i in b["items"]]
        lines += ["",f"Себестоимость: {money(b['cost_total'])}",f"Продажа: {money(b['sale_price'])}",f"Прибыль: {money(b['profit'])}"]
        messagebox.showinfo("PcStorc","\n".join(lines))

    def choose_backup_folder(self):
        p=filedialog.askdirectory(parent=self.root)
        if p:self.backup_folder.set(p)

    def save_backup_settings(self):
        t=self.backup_time.get().strip()
        if len(t)!=5 or t[2] != ":": return messagebox.showerror("PcStorc","Время нужно в формате 21:00")
        self.db.set_setting("backup_folder",self.backup_folder.get().strip()); self.db.set_setting("backup_time",t); self.refresh_backup()

    def manual_backup(self):
        self.save_backup_settings()
        try:p=self.backups.create_backup("manual"); messagebox.showinfo("PcStorc",f"Копия создана:\n{p}"); self.refresh_backup()
        except Exception as e:messagebox.showerror("PcStorc",str(e))

    def restore_backup(self):
        p=filedialog.askopenfilename(parent=self.root,filetypes=[("PcStorc backup","*.zip")])
        if not p:return
        if not messagebox.askyesno("PcStorc","Текущая база будет заменена. Продолжить?"):return
        try:self.backups.restore_backup(p); messagebox.showinfo("PcStorc","База восстановлена. Перезапустите программу."); self.root.destroy()
        except Exception as e:messagebox.showerror("PcStorc",str(e))

    def install_task(self):
        if os.name!="nt":return messagebox.showinfo("PcStorc","Планировщик доступен только в Windows")
        self.save_backup_settings(); exe=Path(sys.executable).resolve(); time=self.backup_time.get()
        cmd=["schtasks","/Create","/F","/SC","DAILY","/TN","PcStorc Daily Backup","/TR",f'"{exe}" --backup',"/ST",time]
        try:subprocess.run(cmd,check=True,capture_output=True,text=True); messagebox.showinfo("PcStorc",f"Ежедневная копия запланирована на {time}")
        except Exception as e:messagebox.showerror("PcStorc",f"Не удалось создать задачу:\n{e}")

    def _daily_check(self):
        try:
            if not self.backups.has_daily_backup_today() and self.backup_time.get()==__import__("datetime").datetime.now().strftime("%H:%M"):
                self.backups.create_backup("daily"); self.refresh_backup()
        finally:self.root.after(60_000,self._daily_check)

    def on_close(self):
        try:
            if self.db.get_setting("backup_on_close","1")=="1": self.backups.create_backup("close")
        except Exception: pass
        try:self.db.close()
        finally:self.root.destroy()


class ComponentDialog(tk.Toplevel):
    def __init__(self,parent):
        super().__init__(parent); self.title("Новая позиция"); self.transient(parent); self.grab_set(); self.result=None
        f=ttk.Frame(self,padding=12); f.pack(fill="both",expand=True)
        self.vars={"category":tk.StringVar(value=DEFAULT_CATEGORIES[0]),"model":tk.StringVar(),"quantity":tk.StringVar(value="0"),"purchase_price":tk.StringVar(value="0"),"supplier":tk.StringVar(),"delivery_days":tk.StringVar(value="1"),"yellow_level":tk.StringVar(value="2"),"red_level":tk.StringVar(value="1")}
        fields=[("Категория","category"),("Модель","model"),("Количество","quantity"),("Цена закупки","purchase_price"),("Поставщик","supplier"),("Доставка, дней","delivery_days"),("Желтый порог","yellow_level"),("Красный порог","red_level")]
        for r,(label,key) in enumerate(fields):
            ttk.Label(f,text=label).grid(row=r,column=0,sticky="w",pady=3)
            w=ttk.Combobox(f,textvariable=self.vars[key],values=DEFAULT_CATEGORIES,state="readonly",width=40) if key=="category" else ttk.Entry(f,textvariable=self.vars[key],width=43)
            w.grid(row=r,column=1,padx=8,pady=3)
        ttk.Button(f,text="Добавить",command=self.save).grid(row=len(fields),column=1,sticky="e",pady=8)
    def save(self):
        try:
            if not self.vars["model"].get().strip():raise PcStorcError("Введите модель")
            self.result={"category":self.vars["category"].get(),"model":self.vars["model"].get(),"quantity":number(self.vars["quantity"].get(),True),"purchase_price":number(self.vars["purchase_price"].get()),"supplier":self.vars["supplier"].get(),"delivery_days":number(self.vars["delivery_days"].get(),True),"yellow_level":number(self.vars["yellow_level"].get(),True),"red_level":number(self.vars["red_level"].get(),True)}; self.destroy()
        except Exception as e:messagebox.showerror("PcStorc",str(e),parent=self)


class BuildDialog(tk.Toplevel):
    def __init__(self,parent,inventory:InventoryService,builds:BuildService,on_saved):
        super().__init__(parent); self.title("Новая сборка"); self.geometry("760x560"); self.transient(parent); self.grab_set()
        self.inventory,self.builds,self.on_saved=inventory,builds,on_saved; self.items=[]; self.components=inventory.list_components()
        f=ttk.Frame(self,padding=12); f.pack(fill="both",expand=True)
        top=ttk.Frame(f); top.pack(fill="x"); self.customer=tk.StringVar(); self.sale=tk.StringVar(value="0")
        ttk.Label(top,text="Клиент:").pack(side="left"); ttk.Entry(top,textvariable=self.customer,width=24).pack(side="left",padx=5); ttk.Label(top,text="Цена продажи:").pack(side="left",padx=(15,0)); ttk.Entry(top,textvariable=self.sale,width=14).pack(side="left",padx=5)
        add=ttk.Frame(f); add.pack(fill="x",pady=10); self.choice=tk.StringVar()
        self.combo=ttk.Combobox(add,textvariable=self.choice,state="readonly",values=[f"{c['category']} — {c['model']} (своб. {c['available']})" for c in self.components],width=65); self.combo.pack(side="left")
        ttk.Button(add,text="Добавить",command=self.add).pack(side="left",padx=5)
        self.list=tk.Listbox(f); self.list.pack(fill="both",expand=True)
        buttons=ttk.Frame(f); buttons.pack(fill="x",pady=8); ttk.Button(buttons,text="Удалить строку",command=self.remove).pack(side="left"); ttk.Button(buttons,text="Создать резерв",command=lambda:self.save("RESERVED")).pack(side="right"); ttk.Button(buttons,text="Сохранить черновик",command=lambda:self.save("DRAFT")).pack(side="right",padx=5)
    def add(self):
        i=self.combo.current()
        if i<0:return
        c=self.components[i]; cost=simpledialog.askfloat("Себестоимость",f"Цена {c['model']}:",initialvalue=c["last_purchase_price"],minvalue=0,parent=self)
        if cost is None:return
        self.items.append(BuildItemInput(c["id"],1,cost)); self.list.insert("end",f"{c['category']}: {c['model']} — {money(cost)}")
    def remove(self):
        s=self.list.curselection()
        if s:self.items.pop(s[0]); self.list.delete(s[0])
    def save(self,status):
        try:self.builds.create_build(self.items,customer=self.customer.get(),sale_price=number(self.sale.get()),status=status); self.on_saved(); self.destroy()
        except Exception as e:messagebox.showerror("PcStorc",str(e),parent=self)
