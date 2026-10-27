#!/usr/bin/env bash
# IndustrialVision AI — Database & Evidence Backup / Restore.
# Usage:
#   ./scripts/backup.sh [--output-dir DIR]           # create timestamped backup
#   ./scripts/restore.sh --backup-file FILE          # restore database
#   ./scripts/restore.sh --backup-file FILE --evidence-dir DIR   # + evidence
#
# Requirements:
#   - pg_dump / pg_restore (PostgreSQL client tools)
#   - read access to the running database container (or host network)
#   - write access to OUTPUT_DIR (default: ./backups)
#
# The script does NOT stop containers; it assumes the database accepts
# connections and that consistent backups are acceptable for the application.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_OUTPUT="${ROOT_DIR}/backups"

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
  --output-dir DIR   Directory to write backup files (default: ${DEFAULT_OUTPUT})
  --compress         gzip the SQL dump (default: true)

Restore options:
  --backup-file FILE  Path to backup file (required)
  --evidence-dir DIR  Also restore evidence volume (default: ${EVIDENCE_ROOT})

Examples:
  ./scripts/backup.sh
  ./scripts/backup.sh --output-dir /mnt/backup-nas
  ./scripts/restore.sh --backup-file ./backups/2026-10-27T14-30-00Z.dump
  ./scripts/restore.sh --backup-file ./backups/2026-10-27T14-30-00Z.dump --evidence-dir /mnt/restore/evidence
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

backup() {
    local output_dir="${1:-${DEFAULT_OUTPUT}}"
    local compress="${2:-true}"

    mkdir -p "${output_dir}"
    local timestamp
    timestamp="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
    local dump_file="${output_dir}/${timestamp}.dump"
    local manifest_file="${output_dir}/${timestamp}.manifest.json"

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

    local size
    size="$(stat -c%s "${dump_file}" 2>/dev/null || stat -f%z "${dump_file}")"
    echo "[backup] Dump size: ${size} bytes"

    cat >"${manifest_file}" <<EOF
{
  "timestamp": "${timestamp}",
  "database": "${POSTGRES_DB}",
  "host": "${POSTGRES_HOST}:${POSTGRES_PORT}",
  "user": "${POSTGRES_USER}",
  "dump_file": "${dump_file}",
  "size_bytes": ${size},
  "evidence_root": "${EVIDENCE_ROOT}",
  "evidence_backed_up": false,
  "schema_version": "alembic_head"
}
EOF

    if [[ "${compress}" == "true" ]]; then
        echo "[backup] Compressing with gzip..."
        gzip -f "${dump_file}"
        dump_file="${dump_file}.gz"
        echo "[backup] Compressed to ${dump_file}"
    fi

    echo "[backup] Manifest written to ${manifest_file}"
    echo "BACKUP_FILE=${dump_file}"
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

    # Evidence restore is intentionally left as a manual step or to be
    # implemented per-site (rsync from off-site, unpack tarball, etc.).
    if [[ -d "${evidence_dir}" ]]; then
        echo "[restore] Evidence directory ${evidence_dir} exists."
        echo "[restore] If you have an evidence backup, extract it to ${evidence_dir} now."
    else
        echo "[restore] Creating evidence directory ${evidence_dir}..."
        mkdir -p "${evidence_dir}"
    fi

    echo "[restore] Done. Remember to run 'alembic upgrade head' if the restored DB schema version differs."
}

main() {
    check_pg_tools

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
            while [[ $# -gt 0 ]]; do
                case $1 in
                    --output-dir) output_dir="${2}"; shift 2 ;;
                    --no-compress) compress="false"; shift ;;
                    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
                esac
            done
            backup "${output_dir}" "${compress}"
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