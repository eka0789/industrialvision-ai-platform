"""Runtime configuration validation and database URL tests."""

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import settings as compatibility_settings
from app.core.config import Settings, settings
from app.db.session import database_connect_args


class SettingsTests(unittest.TestCase):
    def make_settings(self, **overrides):
        return Settings(_env_file=None, **overrides)

    def test_legacy_import_points_to_canonical_settings(self):
        self.assertIs(compatibility_settings, settings)

    def test_defaults_are_consistent_with_sync_sqlite_engine(self):
        config = self.make_settings()
        self.assertEqual(config.DATABASE_URL, "sqlite:///./industrialvision_ai.db")
        self.assertEqual(database_connect_args(config.DATABASE_URL), {"check_same_thread": False})
        self.assertIn("http://localhost:5173", config.cors_origins)

    def test_environment_variables_are_loaded_without_prefix(self):
        with patch.dict(os.environ, {"PORT": "8123", "CORS_ORIGINS": "https://qc.example"}):
            config = Settings(_env_file=None)
        self.assertEqual(config.PORT, 8123)
        self.assertEqual(config.cors_origins, ["https://qc.example"])

    def test_cors_origins_are_normalized(self):
        config = self.make_settings(CORS_ORIGINS=" https://qc.example ,http://localhost:5173,, ")
        self.assertEqual(config.cors_origins, ["https://qc.example", "http://localhost:5173"])

    def test_postgresql_uses_no_sqlite_connection_options(self):
        url = "postgresql+psycopg://user:pass@localhost:5432/industrialvision"
        self.assertEqual(database_connect_args(url), {})
        self.assertEqual(self.make_settings(DATABASE_URL=url).DATABASE_URL, url)

    def test_invalid_backend_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_settings(DATABASE_URL="mysql+pymysql://user:pass@localhost/db")

    def test_async_database_driver_is_rejected_by_sync_engine_config(self):
        with self.assertRaises(ValidationError):
            self.make_settings(DATABASE_URL="sqlite+aiosqlite:///./industrialvision_ai.db")

    def test_production_rejects_default_secret_and_sqlite(self):
        with self.assertRaises(ValidationError):
            self.make_settings(APP_ENV="production")

    def test_production_accepts_explicit_secure_configuration(self):
        config = self.make_settings(
            APP_ENV="production",
            DEBUG=False,
            DEMO_LIVE_FEED=False,
            JWT_SECRET="production-secret-value-that-is-at-least-32-chars",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/industrialvision",
            CORS_ORIGINS="https://inspection.example",
        )
        self.assertEqual(config.APP_ENV, "production")
        self.assertEqual(config.cors_origins, ["https://inspection.example"])

    def test_production_refuses_demo_live_feed(self):
        with self.assertRaises(ValidationError):
            self.make_settings(
                APP_ENV="production",
                DEBUG=False,
                DEMO_LIVE_FEED=True,
                JWT_SECRET="production-secret-value-that-is-at-least-32-chars",
                DATABASE_URL="postgresql+psycopg://user:pass@db:5432/industrialvision",
                CORS_ORIGINS="https://inspection.example",
            )

    def test_production_refuses_debug_log_level(self):
        with self.assertRaises(ValidationError):
            self.make_settings(
                APP_ENV="production",
                DEBUG=False,
                DEMO_LIVE_FEED=False,
                LOG_LEVEL="debug",
                JWT_SECRET="production-secret-value-that-is-at-least-32-chars",
                DATABASE_URL="postgresql+psycopg://user:pass@db:5432/industrialvision",
                CORS_ORIGINS="https://inspection.example",
            )

    def test_production_rejects_example_secret_placeholder(self):
        with self.assertRaises(ValidationError):
            self.make_settings(
                APP_ENV="production",
                DEBUG=False,
                JWT_SECRET="replace-with-a-unique-random-secret-of-at-least-32-characters",
                DATABASE_URL="postgresql+psycopg://user:pass@db:5432/industrialvision",
                CORS_ORIGINS="https://inspection.example",
            )

    def test_production_rejects_wildcard_cors(self):
        with self.assertRaises(ValidationError):
            self.make_settings(
                APP_ENV="production",
                DEBUG=False,
                JWT_SECRET="production-secret-value-that-is-at-least-32-chars",
                DATABASE_URL="postgresql+psycopg://user:pass@db:5432/industrialvision",
                CORS_ORIGINS="*",
            )

    def test_threshold_and_port_ranges_are_validated(self):
        with self.assertRaises(ValidationError):
            self.make_settings(CONF_THRESHOLD=1.5)
        with self.assertRaises(ValidationError):
            self.make_settings(PORT=70000)


if __name__ == "__main__":
    unittest.main()
