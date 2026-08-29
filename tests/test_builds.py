import tempfile
import unittest
from pathlib import Path

from pcstorc.database import Database
from pcstorc.services import BuildItemInput, BuildService, InventoryService, PcStorcError


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.inventory = InventoryService(self.db)
        self.builds = BuildService(self.db, self.inventory)
        self.cpu = self.inventory.add_component("Процессор", "Ryzen 7 9700X", 2, 16275)
        self.gpu = self.inventory.add_component("Видеокарта", "RTX 5060 8GB", 1, 25500)
        self.ssd = self.inventory.add_component("SSD / HDD", "CUSU 1TB M2", 3, 4100)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_reserve_then_sell(self):
        bid = self.builds.create_build([
            BuildItemInput(self.cpu, 1, 16275),
            BuildItemInput(self.gpu, 1, 25500),
            BuildItemInput(self.ssd, 1, 4100),
        ], customer="Тест", sale_price=60000, status="RESERVED")

        self.assertEqual(self.inventory.get_component(self.cpu)["reserved"], 1)
        self.assertEqual(self.inventory.get_component(self.gpu)["available"], 0)

        build = self.builds.get_build(bid)
        self.assertEqual(build["cost_total"], 45875)

        self.builds.sell_build(bid, 60000)
        build = self.builds.get_build(bid)
        self.assertEqual(build["status"], "SOLD")
        self.assertEqual(build["profit"], 14125)
        self.assertEqual(self.inventory.get_component(self.cpu)["quantity"], 1)
        self.assertEqual(self.inventory.get_component(self.gpu)["quantity"], 0)
        self.assertEqual(self.inventory.get_component(self.gpu)["reserved"], 0)

    def test_prevent_over_reserve(self):
        self.builds.create_build([BuildItemInput(self.gpu, 1)], status="RESERVED")
        with self.assertRaises(PcStorcError):
            self.builds.create_build([BuildItemInput(self.gpu, 1)], status="RESERVED")

    def test_quick_match(self):
        matched, unmatched, details = self.builds.quick_match_config(
            "CPU: Ryzen 7 9700X\nRTX 5060 8GB\nCUSU 1TB M2\nкакой-то неизвестный кулер"
        )
        self.assertEqual(len(matched), 3)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(len(details), 3)


if __name__ == "__main__":
    unittest.main()
