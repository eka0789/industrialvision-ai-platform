#!/usr/bin/env bash
# IndustrialVision AI — Database & Evidence Backup / Restore.
# Usage:
#   ./scripts/backup.sh [--output-dir DIR] [--evidence] [--retention-days N]
#                       [--dry-run] [--no-compress]
#   ./scripts/restore.sh --backup-file FILE          # restore database
#   ./scripts/restore.sh --backup-file FILE --evidence-dir DIR   # + evidence
#
# Requirements:
#   - pg_dump / pg_restore (PostgreSQL client tools) — not needed for --dry-run
#   - read access to the running database container (or host network)
#   - write access to OUTPUT_DIR (default: ./backups)
#
# The script does NOT stop containers; it assumes the database accepts
# connections and that consistent backups are acceptable for the application.
# Every backup writes a JSON manifest (schema documented in docs/OPERATIONS.md)
# with SHA-256 checksums so restores can be validated before they are trusted.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_OUTPUT="${ROOT_DIR}/backups"
MANIFEST_SCHEMA_VERSION="2"

# Load .env if present (for local testing)
if [[ -f "${ROOT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env"
    set +a
fi

POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-industrialvision}"
POSTGRES_USER="${POSTGRES_USER:-iv_user}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
EVIDENCE_ROOT="${EVIDENCE_ROOT:-${ROOT_DIR}/backend/data/evidence}"

usage() {
    cat <<EOF
Usage: $(basename "$0") COMMAND [OPTIONS]

Commands:
  backup    Create a timestamped backup of database and (optionally) evidence
  restore   Restore database (and optionally evidence) from a backup file

Backup options:
  --output-dir DIR     Directory to write backup files (default: ${DEFAULT_OUTPUT})
  --evidence           Also archive the evidence volume (tar+gzip) into the backup
  --retention-days N   Delete backups older than N days (default: 30; 0 = keep all)
  --dry-run            Print the plan and validate preconditions; write nothing
  --no-compress        Skip gzip of the SQL dump

Restore options:
  --backup-file FILE  Path to backup file (required)
  --evidence-dir DIR  Also restore evidence volume (default: ${EVIDENCE_ROOT})

Examples:
  ./scripts/backup.sh --evidence --retention-days 14
  ./scripts/backup.sh --output-dir /mnt/backup-nas --dry-run
  ./scripts/restore.sh --backup-file ./backups/2026-11-14T07-00-00Z.dump.gz
EOF
}

check_pg_tools() {
    for tool in pg_dump pg_restore psql; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            echo "ERROR: ${tool} not found in PATH. Install postgresql-client." >&2
            exit 1
        fi
    done
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

count_evidence_files() {
    local root="$1"
    if [[ ! -d "${root}" ]]; then
        echo 0
        return
    fi
    # Files only (excluding the archive itself if it lives under root).
    find "${root}" -type f ! -name "*.evidence.tar.gz" | wc -l | tr -d ' '
}

prune_old_backups() {
    local output_dir="$1"
    local retention_days="$2"
    if [[ "${retention_days}" == "0" ]]; then
        echo "[retention] Disabled (retention-days=0)"
        return
    fi
    if ! command -v find >/dev/null 2>&1; then
        echo "[retention] find unavailable; skipping pruning" >&2
        return
    fi
    # Prune by manifest mtime: each backup set is {timestamp}.dump[.gz] +
    # {timestamp}.manifest.json [+ {timestamp}.evidence.tar.gz].
    local pruned=0
    while IFS= read -r -d '' manifest; do
        local base="${manifest%.manifest.json}"
        local removed=0
        for f in "${base}.dump" "${base}.dump.gz" "${base}.evidence.tar.gz" "${manifest}"; do
            if [[ -f "${f}" ]]; then
                rm -f "${f}"
                removed=1
            fi
        done
        if [[ "${removed}" == "1" ]]; then
            echo "[retention] Pruned backup set: ${base}"
            pruned=$((pruned + 1))
        fi
    done < <(find "${output_dir}" -name "*.manifest.json" -type f -mtime +"${retention_days}" -print0 2>/dev/null)
    if [[ "${pruned}" == "0" ]]; then
        echo "[retention] No backup sets older than ${retention_days} day(s)"
    fi
}

backup() {
    local output_dir="${1:-${DEFAULT_OUTPUT}}"
    local compress="${2:-true}"
    local include_evidence="${3:-false}"
    local retention_days="${4:-30}"
    local dry_run="${5:-false}"

    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
    local dump_file="${output_dir}/${timestamp}.dump"
    local evidence_archive="${output_dir}/${timestamp}.evidence.tar.gz"
    local manifest_file="${output_dir}/${timestamp}.manifest.json"

    if [[ "${dry_run}" == "true" ]]; then
        echo "[dry-run] Would write dump:      ${dump_file}$( [[ "${compress}" == "true" ]] && echo '.gz' )"
        if [[ "${include_evidence}" == "true" ]]; then
            if [[ ! -d "${EVIDENCE_ROOT}" ]]; then
                echo "[dry-run] WARNING: evidence root ${EVIDENCE_ROOT} does not exist"
            else
                echo "[dry-run] Would archive evidence: ${EVIDENCE_ROOT} ($(count_evidence_files "${EVIDENCE_ROOT}") files) -> ${evidence_archive}"
            fi
        fi
        echo "[dry-run] Would write manifest:  ${manifest_file}"
        echo "[dry-run] Would prune backups older than ${retention_days} day(s) in ${output_dir}"
        echo "[dry-run] OK (nothing written)"
        return 0
    fi

    mkdir -p "${output_dir}"

    echo "[backup] Writing custom-format dump to ${dump_file}..."
    PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
        --host="${POSTGRES_HOST}" \
        --port="${POSTGRES_PORT}" \
        --username="${POSTGRES_USER}" \
        --dbname="${POSTGRES_DB}" \
        --format=custom \
        --no-owner \
        --no-privileges \
        --file="${dump_file}"

    if [[ "${compress}" == "true" ]]; then
        echo "[backup] Compressing with gzip..."
        gzip -f "${dump_file}"
        dump_file="${dump_file}.gz"
    fi

    local dump_sha size
    dump_sha="$(sha256_of "${dump_file}")"
    size="$(stat -c%s "${dump_file}" 2>/dev/null || stat -f%z "${dump_file}")"
    echo "[backup] Dump: ${dump_file} (${size} bytes, sha256=${dump_sha:0:12}...)"

    local evidence_json="null"
    if [[ "${include_evidence}" == "true" ]]; then
        if [[ ! -d "${EVIDENCE_ROOT}" ]]; then
            echo "[backup] WARNING: evidence root ${EVIDENCE_ROOT} not found; skipping evidence archive" >&2
        else
            echo "[backup] Archiving evidence volume ${EVIDENCE_ROOT}..."
            tar -czf "${evidence_archive}" -C "${EVIDENCE_ROOT}" .
            local evidence_sha evidence_files evidence_size
            evidence_sha="$(sha256_of "${evidence_archive}")"
            evidence_files="$(count_evidence_files "${EVIDENCE_ROOT}")"
            evidence_size="$(stat -c%s "${evidence_archive}" 2>/dev/null || stat -f%z "${evidence_archive}")"
            evidence_json=$(cat <<EOF
{
      "root": "${EVIDENCE_ROOT}",
      "archive": "${evidence_archive}",
      "size_bytes": ${evidence_size},
      "sha256": "${evidence_sha}",
      "file_count": ${evidence_files}
    }
EOF
)
            echo "[backup] Evidence archive: ${evidence_archive} (${evidence_files} files)"
        fi
    fi

    python - "${manifest_file}" "${MANIFEST_SCHEMA_VERSION}" "${timestamp}" "${POSTGRES_DB}" \
        "${POSTGRES_HOST}:${POSTGRES_PORT}" "${POSTGRES_USER}" "${dump_file}" "${size}" "${dump_sha}" \
        "${include_evidence}" "${retention_days}" "${evidence_json}" <<'PYEOF'
import json, sys

manifest_file, schema_version, timestamp, database, host, user, dump_file, size, dump_sha, evidence_flag, retention_days, evidence_json = sys.argv[1:]
manifest = {
    "schema_version": int(schema_version),
    "timestamp": timestamp,
    "database": database,
    "host": host,
    "user": user,
    "dump_file": dump_file,
    "size_bytes": int(size),
    "dump_sha256": dump_sha,
    "evidence": json.loads(evidence_json) if evidence_json != "null" else None,
    "retention_days": int(retention_days),
}
with open(manifest_file, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
PYEOF

    echo "[backup] Manifest written to ${manifest_file}"
    prune_old_backups "${output_dir}" "${retention_days}"
    echo "BACKUP_FILE=${dump_file}"
    echo "MANIFEST_FILE=${manifest_file}"
}

restore() {
    local backup_file="${1:-}"
    local evidence_dir="${2:-${EVIDENCE_ROOT}}"

    if [[ -z "${backup_file}" ]]; then
        echo "ERROR: --backup-file is required for restore" >&2
        usage
        exit 1
    fi

    if [[ ! -f "${backup_file}" ]]; then
        echo "ERROR: Backup file not found: ${backup_file}" >&2
        exit 1
    fi

    # Manifest verification (restore drill step 1: trust nothing unchecked).
    local manifest_file="${backup_file%.*}"
    manifest_file="${backup_file%.gz}"
    manifest_file="${manifest_file%.dump}.manifest.json"
    if [[ -f "${manifest_file}" ]]; then
        echo "[restore] Verifying manifest ${manifest_file}..."
        python - "${backup_file}" "${manifest_file}" <<'PYEOF'
import hashlib, json, sys

backup_file, manifest_file = sys.argv[1], sys.argv[2]
with open(manifest_file, encoding="utf-8") as fh:
    manifest = json.load(fh)
if manifest.get("schema_version", 0) < 2:
    raise SystemExit("[restore] ERROR: manifest schema too old; regenerate backups")
h = hashlib.sha256()
with open(backup_file, "rb") as fh:
    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
        h.update(chunk)
if h.hexdigest() != manifest.get("dump_sha256"):
    raise SystemExit("[restore] ERROR: dump sha256 does not match manifest — backup is corrupt or was tampered with")
print("[restore] Checksum OK:", manifest["dump_sha256"][:12], "...")
PYEOF
    else
        echo "[restore] WARNING: no manifest found next to ${backup_file}; cannot verify checksum" >&2
    fi

    echo "[restore] Restoring database from ${backup_file}..."

    # If gzipped, decompress to a temp file
    local restore_file="${backup_file}"
    local temp_file=""
    if [[ "${backup_file}" == *.gz ]]; then
        temp_file="$(mktemp --suffix=.dump)"
        gunzip -c "${backup_file}" >"${temp_file}"
        restore_file="${temp_file}"
    fi

    PGPASSWORD="${POSTGRES_PASSWORD}" pg_restore \
        --host="${POSTGRES_HOST}" \
        --port="${POSTGRES_PORT}" \
        --username="${POSTGRES_USER}" \
        --dbname="${POSTGRES_DB}" \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        --format=custom \
        "${restore_file}"

    if [[ -n "${temp_file}" ]]; then
        rm -f "${temp_file}"
    fi

    echo "[restore] Database restored."

    local evidence_archive="${backup_file%.dump.gz}.evidence.tar.gz"
    evidence_archive="${evidence_archive%.dump}.evidence.tar.gz"
    if [[ -f "${evidence_archive}" ]]; then
        echo "[restore] Evidence archive found: ${evidence_archive}"
        echo "[restore] Restoring into ${evidence_dir}..."
        mkdir -p "${evidence_dir}"
        tar -xzf "${evidence_archive}" -C "${evidence_dir}"
        echo "[restore] Evidence restored. Verify file counts against the manifest."
    elif [[ -d "${evidence_dir}" ]]; then
        echo "[restore] No evidence archive beside the dump; ${evidence_dir} left untouched."
    else
        echo "[restore] Creating evidence directory ${evidence_dir}..."
        mkdir -p "${evidence_dir}"
    fi

    echo "[restore] Done. Remember to run 'alembic upgrade head' if the restored DB schema version differs."
}

main() {
    if [[ $# -eq 0 ]]; then
        usage
        exit 1
    fi

    local cmd="${1}"
    shift

    case "${cmd}" in
        backup)
            local output_dir="${DEFAULT_OUTPUT}"
            local compress="true"
            local include_evidence="false"
            local retention_days="30"
            local dry_run="false"
            while [[ $# -gt 0 ]]; do
                case $1 in
                    --output-dir) output_dir="${2}"; shift 2 ;;
                    --no-compress) compress="false"; shift ;;
                    --evidence) include_evidence="true"; shift ;;
                    --retention-days) retention_days="${2}"; shift 2 ;;
                    --dry-run) dry_run="true"; shift ;;
                    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
                esac
            done
            if [[ "${dry_run}" != "true" ]]; then
                check_pg_tools
            fi
            backup "${output_dir}" "${compress}" "${include_evidence}" "${retention_days}" "${dry_run}"
            ;;
        restore)
            local backup_file=""
            local evidence_dir="${EVIDENCE_ROOT}"
            while [[ $# -gt 0 ]]; do
                case $1 in
                    --backup-file) backup_file="${2}"; shift 2 ;;
                    --evidence-dir) evidence_dir="${2}"; shift 2 ;;
                    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
                esac
            done
            check_pg_tools
            restore "${backup_file}" "${evidence_dir}"
            ;;
        *)
            echo "Unknown command: ${cmd}" >&2
            usage
            exit 1
            ;;
    esac
}

main "$@"
