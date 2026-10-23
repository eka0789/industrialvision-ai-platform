"""Run Alembic upgrade/downgrade against an isolated temporary SQLite DB."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import Float, create_engine, inspect


BACKEND_DIR = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def test_clean_upgrade_and_downgrade(self):
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "migration-test.sqlite3"
            engine = create_engine(f"sqlite:///{database_path}")
            try:
                with engine.connect() as connection:
                    config = Config(str(BACKEND_DIR / "alembic.ini"))
                    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
                    config.set_main_option("version_locations", str(BACKEND_DIR / "migrations" / "versions"))
                    config.attributes["connection"] = connection

                    command.upgrade(config, "head")
                    inspector = inspect(connection)
                    tables = set(inspector.get_table_names())
                    self.assertTrue(
                        {
                            "roles",
                            "users",
                            "cameras",
                            "inspection_sessions",
                            "inspections",
                            "detection_results",
                            "alembic_version",
                        }.issubset(tables)
                    )
                    camera_columns = {
                        column["name"]: column["type"]
                        for column in inspector.get_columns("cameras")
                    }
                    self.assertIsInstance(camera_columns["fps_input"], Float)
                    checks = inspector.get_check_constraints("inspections")
                    self.assertIn("ck_inspections_decision", {check["name"] for check in checks})

                    command.downgrade(config, "base")
                    self.assertEqual(
                        set(inspect(connection).get_table_names()), {"alembic_version"}
                    )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
