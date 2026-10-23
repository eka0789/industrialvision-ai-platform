"""Evidence storage path, persistence, and deletion safety tests."""

import tempfile
import unittest
from pathlib import Path

from app.services.evidence import EvidenceStorage


class EvidenceStorageTests(unittest.TestCase):
    def test_save_uses_server_generated_relative_path_and_resolves(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "evidence"
            storage = EvidenceStorage(root)
            relative_path = storage.save_image(42, b"validated image bytes", ".jpg")
            self.assertFalse(Path(relative_path).is_absolute())
            self.assertNotIn("..", Path(relative_path).parts)
            self.assertTrue(storage.resolve(relative_path).is_file())
            self.assertEqual(storage.resolve(relative_path).read_bytes(), b"validated image bytes")

    def test_rejects_invalid_ids_extensions_and_empty_payloads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EvidenceStorage(temp_dir)
            with self.assertRaises(ValueError):
                storage.save_image(-1, b"image", ".jpg")
            with self.assertRaises(ValueError):
                storage.save_image(1, b"image", ".txt")
            with self.assertRaises(ValueError):
                storage.save_image(1, b"", ".jpg")

    def test_resolve_and_delete_reject_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EvidenceStorage(Path(temp_dir) / "evidence")
            outside = Path(temp_dir) / "secret.txt"
            outside.write_text("do not delete", encoding="utf-8")
            with self.assertRaises(ValueError):
                storage.resolve("../secret.txt")
            with self.assertRaises(ValueError):
                storage.delete("../secret.txt")
            self.assertTrue(outside.exists())

    def test_delete_removes_owned_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = EvidenceStorage(temp_dir)
            relative_path = storage.save_image(7, b"image", ".png")
            self.assertTrue(storage.delete(relative_path))
            self.assertFalse(storage.delete(relative_path))


if __name__ == "__main__":
    unittest.main()
