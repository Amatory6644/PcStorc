import tempfile
import unittest
import zipfile
from pathlib import Path

from pcstorc.backup import BackupManager
from pcstorc.database import Database
from pcstorc.services import InventoryService


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = Database(root / "test.db")
        self.db.set_setting("backup_folder", str(root / "backups"))
        self.db.set_setting("backup_retention", "2")
        self.inventory = InventoryService(self.db)
        self.backups = BackupManager(self.db)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_backup_contains_db_and_csv(self):
        self.inventory.add_component("Блок питания", "PowerCase 700W", 2, 3350)
        path = self.backups.create_backup("manual")
        self.assertTrue(path.exists())
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            self.assertIn("pcstorc.db", names)
            self.assertIn("inventory.csv", names)
            self.assertIn("builds.csv", names)
            self.assertIn("movements.csv", names)
            self.assertIn("backup_info.json", names)


if __name__ == "__main__":
    unittest.main()
