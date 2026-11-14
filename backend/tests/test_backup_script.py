"""Backup script tests: dry-run safety, manifest schema, retention pruning.

The real pg_dump path runs against a stub `pg_dump` on PATH that writes a
small valid file, so the full backup flow (dump -> checksum -> manifest ->
retention) is exercised without a PostgreSQL server.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO / "scripts" / "backup.sh"


def _find_bash() -> str:
    """Prefer Git Bash over WSL bash on Windows; plain 'bash' elsewhere."""
    for candidate in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return "bash"


BASH = _find_bash()


def _posix(path: Path | str) -> str:
    """Git Bash tools (tar) need /c/... style paths, not C:\\..."""
    text = str(path).replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return f"/{text[0].lower()}{text[2:]}"
    return text


def _make_stub_pg_tools(bin_dir: Path) -> None:
    """Write minimal pg_dump/pg_restore/psql shims so the script can run."""
    for name, body in (
        ("pg_dump", '#!/usr/bin/env bash\nout=""\nfor arg in "$@"; do\n  case "$arg" in --file=*) out="${arg#--file=}" ;; esac\ndone\nif [[ -z "$out" ]]; then echo "pg_dump stub: no --file= given" >&2; exit 1; fi\nprintf "PGDMP-STUB" > "$out"\n'),
        ("pg_restore", '#!/usr/bin/env bash\nexit 0\n'),
        ("psql", '#!/usr/bin/env bash\nexit 0\n'),
    ):
        path = bin_dir / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class BackupScriptTests(unittest.TestCase):
    def run_script(self, args: list[str], bin_dir: Path | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["POSTGRES_HOST"] = "stubhost"
        env["POSTGRES_PORT"] = "5432"
        env["POSTGRES_DB"] = "stubdb"
        env["POSTGRES_USER"] = "stubuser"
        if bin_dir is not None:
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            [BASH, str(BACKUP_SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_dry_run_writes_nothing_and_does_not_need_pg_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "backups"
            result = self.run_script(["backup", "--output-dir", _posix(out_dir), "--dry-run", "--evidence"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[dry-run] OK (nothing written)", result.stdout)
            self.assertFalse(out_dir.exists(), "dry-run must not create the output directory")

    def test_backup_writes_dump_evidence_and_schema_v2_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _make_stub_pg_tools(bin_dir)
            evidence_root = Path(tmp) / "evidence"
            (evidence_root / "cam-1").mkdir(parents=True)
            (evidence_root / "cam-1" / "insp.bin").write_bytes(b"\x00\x01evidence")
            (evidence_root / "cam-1" / "meta.json").write_text("{}", encoding="utf-8")

            out_dir = Path(tmp) / "backups"
            env_dir = f"{tmp}/bin"
            env = os.environ.copy()
            env["PATH"] = f"{env_dir}{os.pathsep}{env.get('PATH', '')}"
            env["EVIDENCE_ROOT"] = _posix(evidence_root)
            env["POSTGRES_HOST"] = "stubhost"
            env["POSTGRES_DB"] = "stubdb"
            env["POSTGRES_USER"] = "stubuser"
            result = subprocess.run(
                [BASH, str(BACKUP_SCRIPT), "backup", "--output-dir", _posix(out_dir), "--evidence", "--no-compress"],
                capture_output=True, text=True, env=env, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            manifests = list(out_dir.glob("*.manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))

            # Manifest schema v2: required keys with correct types.
            self.assertEqual(manifest["schema_version"], 2)
            for key in ("timestamp", "database", "host", "user", "dump_file", "dump_sha256"):
                self.assertIsInstance(manifest[key], str)
                self.assertTrue(manifest[key])
            self.assertIsInstance(manifest["size_bytes"], int)
            self.assertIsInstance(manifest["retention_days"], int)
            self.assertRegex(manifest["dump_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(manifest["database"], "stubdb")

            evidence = manifest["evidence"]
            self.assertIsNotNone(evidence, "--evidence must populate the evidence block")
            self.assertEqual(evidence["file_count"], 2)
            self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(evidence["size_bytes"], int)

            dump_path = Path(manifest["dump_file"])
            self.assertTrue(dump_path.exists())
            self.assertTrue((out_dir / dump_path.name).exists())
            archives = list(out_dir.glob("*.evidence.tar.gz"))
            self.assertEqual(len(archives), 1)

    def test_retention_prunes_old_backup_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _make_stub_pg_tools(bin_dir)
            out_dir = Path(tmp) / "backups"
            out_dir.mkdir()

            # Fake an old backup set (2 days old).
            old_base = out_dir / "2026-01-01T00-00-00Z"
            old_base.with_suffix(".dump").write_bytes(b"PGDMP-OLD")
            old_base.with_suffix(".manifest.json").write_text(
                json.dumps({"schema_version": 2, "timestamp": "2026-01-01T00-00-00Z"}), encoding="utf-8"
            )
            two_days_ago = time.time() - 2 * 86400
            os.utime(old_base.with_suffix(".dump"), (two_days_ago, two_days_ago))
            os.utime(old_base.with_suffix(".manifest.json"), (two_days_ago, two_days_ago))

            result = self.run_script(
                ["backup", "--output-dir", _posix(out_dir), "--no-compress", "--retention-days", "1"],
                bin_dir=bin_dir,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[retention] Pruned backup set", result.stdout)
            self.assertFalse(old_base.with_suffix(".dump").exists())
            self.assertFalse(old_base.with_suffix(".manifest.json").exists())
            # Exactly the new backup set remains.
            self.assertEqual(len(list(out_dir.glob("*.manifest.json"))), 1)

    def test_retention_disabled_keeps_old_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _make_stub_pg_tools(bin_dir)
            out_dir = Path(tmp) / "backups"
            out_dir.mkdir()
            old_manifest = out_dir / "2026-01-01T00-00-00Z.manifest.json"
            old_manifest.write_text("{}", encoding="utf-8")
            two_days_ago = time.time() - 2 * 86400
            os.utime(old_manifest, (two_days_ago, two_days_ago))

            result = self.run_script(
                ["backup", "--output-dir", _posix(out_dir), "--no-compress", "--retention-days", "0"],
                bin_dir=bin_dir,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[retention] Disabled", result.stdout)
            self.assertTrue(old_manifest.exists())

    def test_missing_pg_tools_fail_in_real_mode(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            empty_bin = Path(tmp) / "emptybin"
            empty_bin.mkdir()
            # Resolve the interpreter BEFORE wiping PATH (on Linux "bash" only
            # resolves via the original PATH; the script itself must still fail
            # on missing pg_dump, not on a missing shell).
            bash_absolute = shutil.which("bash") or BASH
            env = os.environ.copy()
            env["PATH"] = str(empty_bin)  # no pg_dump anywhere
            result = subprocess.run(
                [bash_absolute, str(BACKUP_SCRIPT), "backup", "--output-dir", _posix(tmp)],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pg_dump not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
