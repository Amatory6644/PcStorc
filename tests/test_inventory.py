import tempfile
import unittest
from pathlib import Path

from pcstorc.database import Database
from pcstorc.services import InventoryService, PcStorcError


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.svc = InventoryService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_receive_and_adjust(self):
        cid = self.svc.add_component(
            "Процессор", "Ryzen 7 9700X", quantity=2, purchase_price=16000,
            supplier="DNS", delivery_days=1, yellow_level=2, red_level=1,
        )
        item = self.svc.get_component(cid)
        self.assertEqual(item["quantity"], 2)
        self.assertEqual(item["stock_status"], "YELLOW")

        self.svc.receive_stock(cid, 3, 15500, "Ozon")
        item = self.svc.get_component(cid)
        self.assertEqual(item["quantity"], 5)
        self.assertEqual(item["last_purchase_price"], 15500)
        self.assertEqual(item["supplier"], "Ozon")
        self.assertEqual(item["stock_status"], "GREEN")

        self.svc.adjust_stock(cid, 4, "Инвентаризация")
        self.assertEqual(self.svc.get_component(cid)["quantity"], 4)
        self.assertEqual(len(self.svc.list_movements()), 3)

    def test_invalid_thresholds(self):
        with self.assertRaises(PcStorcError):
            self.svc.add_component("SSD / HDD", "SSD 1TB", yellow_level=1, red_level=2)


if __name__ == "__main__":
    unittest.main()
