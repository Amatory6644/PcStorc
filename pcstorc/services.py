from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import re
from typing import Iterable, Sequence

from .database import Database

DEFAULT_CATEGORIES = [
    "Процессор",
    "Материнская плата",
    "Оперативная память",
    "Видеокарта",
    "Охлаждение",
    "SSD / HDD",
    "Блок питания",
    "Корпус",
    "Вентиляторы",
    "Кабели / переходники",
    "Другое",
]


class PcStorcError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildItemInput:
    component_id: int
    quantity: int = 1
    unit_cost: float | None = None


def _normalize(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split())


class InventoryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_components(self, include_inactive: bool = False) -> list[dict]:
        where = "" if include_inactive else "WHERE c.active = 1"
        rows = self.db.connection.execute(
            f"""
            SELECT c.*,
                   COALESCE(r.reserved, 0) AS reserved,
                   c.quantity - COALESCE(r.reserved, 0) AS available
            FROM components c
            LEFT JOIN (
                SELECT bi.component_id, SUM(bi.quantity) AS reserved
                FROM build_items bi
                JOIN builds b ON b.id = bi.build_id
                WHERE b.status = 'RESERVED'
                GROUP BY bi.component_id
            ) r ON r.component_id = c.id
            {where}
            ORDER BY c.category COLLATE NOCASE, c.model COLLATE NOCASE
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["stock_status"] = self.stock_status(item["available"], item["yellow_level"], item["red_level"])
            item["urgency"] = self.urgency(item)
            result.append(item)
        return result

    def get_component(self, component_id: int) -> dict:
        for item in self.list_components(include_inactive=True):
            if item["id"] == component_id:
                return item
        raise PcStorcError("Комплектующая не найдена")

    @staticmethod
    def stock_status(available: int, yellow_level: int, red_level: int) -> str:
        if available <= red_level:
            return "RED"
        if available <= yellow_level:
            return "YELLOW"
        return "GREEN"

    @staticmethod
    def urgency(item: dict) -> str:
        status = item["stock_status"]
        days = int(item["delivery_days"])
        if status == "RED" and days >= 7:
            return "Заказать срочно"
        if status == "RED":
            return "Заказать"
        if status == "YELLOW" and days >= 7:
            return "Заказать заранее"
        if status == "YELLOW":
            return "Контроль"
        return "ОК"

    def add_component(
        self,
        category: str,
        model: str,
        quantity: int = 0,
        purchase_price: float = 0,
        supplier: str = "",
        delivery_days: int = 1,
        yellow_level: int = 2,
        red_level: int = 1,
        notes: str = "",
    ) -> int:
        category = category.strip()
        model = model.strip()
        if not category or not model:
            raise PcStorcError("Категория и модель обязательны")
        if quantity < 0 or purchase_price < 0 or delivery_days < 0 or red_level < 0 or yellow_level < 0:
            raise PcStorcError("Числовые значения не могут быть отрицательными")
        if red_level > yellow_level:
            raise PcStorcError("Красный порог не должен быть выше желтого")
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO components(
                        category, model, quantity, last_purchase_price, supplier,
                        delivery_days, yellow_level, red_level, notes
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (category, model, quantity, purchase_price, supplier.strip(), delivery_days, yellow_level, red_level, notes.strip()),
                )
                component_id = int(cur.lastrowid)
                if quantity:
                    conn.execute(
                        """
                        INSERT INTO movements(component_id, kind, quantity, unit_price, note)
                        VALUES(?, 'OPENING', ?, ?, 'Начальный остаток')
                        """,
                        (component_id, quantity, purchase_price),
                    )
            return component_id
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise PcStorcError("Такая модель уже есть в этой категории") from exc
            raise

    def update_component(
        self,
        component_id: int,
        *,
        category: str,
        model: str,
        supplier: str,
        delivery_days: int,
        yellow_level: int,
        red_level: int,
        notes: str,
        last_purchase_price: float,
    ) -> None:
        if red_level > yellow_level:
            raise PcStorcError("Красный порог не должен быть выше желтого")
        if min(delivery_days, yellow_level, red_level) < 0 or last_purchase_price < 0:
            raise PcStorcError("Числовые значения не могут быть отрицательными")
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE components SET category=?, model=?, supplier=?, delivery_days=?,
                    yellow_level=?, red_level=?, notes=?, last_purchase_price=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    category.strip(), model.strip(), supplier.strip(), delivery_days,
                    yellow_level, red_level, notes.strip(), last_purchase_price, component_id,
                ),
            )

    def receive_stock(self, component_id: int, quantity: int, unit_price: float, supplier: str = "", note: str = "") -> None:
        if quantity <= 0:
            raise PcStorcError("Количество прихода должно быть больше нуля")
        if unit_price < 0:
            raise PcStorcError("Цена не может быть отрицательной")
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE components
                SET quantity = quantity + ?, last_purchase_price = ?,
                    supplier = CASE WHEN ? <> '' THEN ? ELSE supplier END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (quantity, unit_price, supplier.strip(), supplier.strip(), component_id),
            )
            conn.execute(
                """
                INSERT INTO movements(component_id, kind, quantity, unit_price, note)
                VALUES(?, 'RECEIPT', ?, ?, ?)
                """,
                (component_id, quantity, unit_price, note.strip() or "Приход товара"),
            )

    def adjust_stock(self, component_id: int, new_quantity: int, note: str) -> None:
        if new_quantity < 0:
            raise PcStorcError("Остаток не может быть отрицательным")
        current = self.get_component(component_id)
        if new_quantity < current["reserved"]:
            raise PcStorcError(
                f"Нельзя поставить остаток {new_quantity}: в резерве уже {current['reserved']} шт."
            )
        diff = new_quantity - current["quantity"]
        if diff == 0:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE components SET quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_quantity, component_id),
            )
            conn.execute(
                """
                INSERT INTO movements(component_id, kind, quantity, unit_price, note)
                VALUES(?, 'ADJUSTMENT', ?, 0, ?)
                """,
                (component_id, diff, note.strip() or "Ручная корректировка"),
            )

    def set_active(self, component_id: int, active: bool) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE components SET active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if active else 0, component_id),
            )

    def list_movements(self, limit: int = 1000) -> list[dict]:
        rows = self.db.connection.execute(
            """
            SELECT m.*, c.category, c.model
            FROM movements m
            JOIN components c ON c.id = m.component_id
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class BuildService:
    def __init__(self, db: Database, inventory: InventoryService | None = None) -> None:
        self.db = db
        self.inventory = inventory or InventoryService(db)

    def _next_code(self) -> str:
        prefix = datetime.now().strftime("PC-%Y%m%d-")
        row = self.db.connection.execute(
            "SELECT code FROM builds WHERE code LIKE ? ORDER BY code DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        n = 1
        if row:
            try:
                n = int(row["code"].rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{n:03d}"

    def create_build(
        self,
        items: Sequence[BuildItemInput],
        *,
        customer: str = "",
        description: str = "",
        sale_price: float = 0,
        notes: str = "",
        status: str = "RESERVED",
    ) -> int:
        if not items:
            raise PcStorcError("Добавьте хотя бы одну комплектующую")
        if status not in {"DRAFT", "RESERVED"}:
            raise PcStorcError("Новая сборка может быть только черновиком или резервом")
        if sale_price < 0:
            raise PcStorcError("Цена продажи не может быть отрицательной")

        components = {c["id"]: c for c in self.inventory.list_components(include_inactive=True)}
        requested: dict[int, int] = {}
        for item in items:
            if item.component_id not in components:
                raise PcStorcError("В конфигурации есть неизвестная комплектующая")
            if item.quantity <= 0:
                raise PcStorcError("Количество должно быть больше нуля")
            requested[item.component_id] = requested.get(item.component_id, 0) + item.quantity

        if status == "RESERVED":
            for component_id, qty in requested.items():
                c = components[component_id]
                if c["available"] < qty:
                    raise PcStorcError(
                        f"Недостаточно '{c['model']}': доступно {c['available']}, нужно {qty}"
                    )

        code = self._next_code()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO builds(code, customer, description, status, sale_price, notes, reserved_at)
                VALUES(?, ?, ?, ?, ?, ?, CASE WHEN ?='RESERVED' THEN CURRENT_TIMESTAMP ELSE NULL END)
                """,
                (code, customer.strip(), description.strip(), status, sale_price, notes.strip(), status),
            )
            build_id = int(cur.lastrowid)
            for item in items:
                c = components[item.component_id]
                cost = c["last_purchase_price"] if item.unit_cost is None else item.unit_cost
                if cost < 0:
                    raise PcStorcError("Себестоимость не может быть отрицательной")
                conn.execute(
                    """
                    INSERT INTO build_items(
                        build_id, component_id, quantity, unit_cost,
                        category_snapshot, component_name_snapshot
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (build_id, item.component_id, item.quantity, cost, c["category"], c["model"]),
                )
        return build_id

    def list_builds(self) -> list[dict]:
        rows = self.db.connection.execute(
            """
            SELECT b.*,
                   COALESCE(SUM(bi.quantity * bi.unit_cost), 0) AS cost_total,
                   b.sale_price - COALESCE(SUM(bi.quantity * bi.unit_cost), 0) AS profit,
                   COALESCE(SUM(bi.quantity), 0) AS item_count
            FROM builds b
            LEFT JOIN build_items bi ON bi.build_id = b.id
            GROUP BY b.id
            ORDER BY b.id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_build(self, build_id: int) -> dict:
        build = next((b for b in self.list_builds() if b["id"] == build_id), None)
        if not build:
            raise PcStorcError("Сборка не найдена")
        build["items"] = self.list_build_items(build_id)
        return build

    def list_build_items(self, build_id: int) -> list[dict]:
        rows = self.db.connection.execute(
            """
            SELECT bi.*, c.quantity AS stock_quantity,
                   COALESCE(r.reserved, 0) AS reserved_total
            FROM build_items bi
            JOIN components c ON c.id = bi.component_id
            LEFT JOIN (
                SELECT bi2.component_id, SUM(bi2.quantity) AS reserved
                FROM build_items bi2
                JOIN builds b2 ON b2.id = bi2.build_id
                WHERE b2.status='RESERVED'
                GROUP BY bi2.component_id
            ) r ON r.component_id = bi.component_id
            WHERE bi.build_id = ?
            ORDER BY bi.id
            """,
            (build_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def sell_build(self, build_id: int, sale_price: float) -> None:
        if sale_price < 0:
            raise PcStorcError("Цена продажи не может быть отрицательной")
        build = self.get_build(build_id)
        if build["status"] != "RESERVED":
            raise PcStorcError("Продать можно только сборку в резерве")
        items = build["items"]
        # Check total physical stock against all reservations before consuming this build.
        for item in items:
            if item["stock_quantity"] < item["reserved_total"]:
                raise PcStorcError(
                    f"Не хватает '{item['component_name_snapshot']}': на складе {item['stock_quantity']}, "
                    f"а зарезервировано {item['reserved_total']}"
                )
        with self.db.transaction() as conn:
            for item in items:
                conn.execute(
                    "UPDATE components SET quantity=quantity-?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (item["quantity"], item["component_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO movements(
                        component_id, kind, quantity, unit_price,
                        reference_type, reference_id, note
                    ) VALUES(?, 'SALE', ?, ?, 'BUILD', ?, ?)
                    """,
                    (
                        item["component_id"], -item["quantity"], item["unit_cost"], build_id,
                        f"Продажа сборки {build['code']}",
                    ),
                )
            conn.execute(
                """
                UPDATE builds SET status='SOLD', sale_price=?, sold_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (sale_price, build_id),
            )

    def cancel_build(self, build_id: int) -> None:
        build = self.get_build(build_id)
        if build["status"] not in {"DRAFT", "RESERVED"}:
            raise PcStorcError("Нельзя отменить уже проданную сборку")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE builds SET status='CANCELED', canceled_at=CURRENT_TIMESTAMP WHERE id=?",
                (build_id,),
            )

    def reserve_draft(self, build_id: int) -> None:
        build = self.get_build(build_id)
        if build["status"] != "DRAFT":
            raise PcStorcError("В резерв можно перевести только черновик")
        inventory = {c["id"]: c for c in self.inventory.list_components(include_inactive=True)}
        for item in build["items"]:
            c = inventory[item["component_id"]]
            if c["available"] < item["quantity"]:
                raise PcStorcError(
                    f"Недостаточно '{c['model']}': доступно {c['available']}, нужно {item['quantity']}"
                )
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE builds SET status='RESERVED', reserved_at=CURRENT_TIMESTAMP WHERE id=?",
                (build_id,),
            )

    def quick_match_config(self, text: str) -> tuple[list[BuildItemInput], list[str], list[dict]]:
        """Match one pasted config line to at most one existing component.

        Returns (matched items, unmatched lines, match details).
        """
        components = self.inventory.list_components()
        normalized_components = [(_normalize(c["model"]), c) for c in components]
        matched: list[BuildItemInput] = []
        unmatched: list[str] = []
        details: list[dict] = []
        used_ids: set[int] = set()

        for raw_line in text.splitlines():
            line = raw_line.strip(" \t•-–—;,")
            if not line:
                continue
            norm = _normalize(line)
            best_score = 0.0
            best_component = None
            for model_norm, component in normalized_components:
                if component["id"] in used_ids:
                    continue
                if not model_norm:
                    continue
                if model_norm in norm or norm in model_norm:
                    score = 1.0 if model_norm == norm else 0.92
                else:
                    score = SequenceMatcher(None, norm, model_norm).ratio()
                # Token overlap helps lines such as "CPU: Ryzen 7 9700X".
                a = set(norm.split())
                b = set(model_norm.split())
                if a and b:
                    overlap = len(a & b) / len(b)
                    score = max(score, overlap * 0.95)
                if score > best_score:
                    best_score = score
                    best_component = component
            if best_component is not None and best_score >= 0.58:
                matched.append(BuildItemInput(best_component["id"], 1, best_component["last_purchase_price"]))
                details.append({"line": line, "component": best_component, "score": best_score})
                used_ids.add(best_component["id"])
            else:
                unmatched.append(line)
        return matched, unmatched, details
