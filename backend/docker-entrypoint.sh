#!/usr/bin/env bash
# Entrypoint for IndustrialVision AI backend container.
# Runs alembic upgrade head before starting the application server.
# Fails fast if migrations cannot be applied.

set -euo pipefail

# Allow running without migrations in development (when APP_ENV=development
# and MIGRATE_ON_START is not explicitly set to true).
if [[ "${APP_ENV:-development}" == "production" ]] || [[ "${MIGRATE_ON_START:-false}" == "true" ]]; then
    echo "[entrypoint] Running database migrations..."
    if ! alembic upgrade head; then
        echo "[entrypoint] ERROR: Migrations failed. Container will not start." >&2
        exit 1
    fi
    echo "[entrypoint] Migrations applied successfully."
else
    echo "[entrypoint] Skipping migrations (APP_ENV=${APP_ENV}, MIGRATE_ON_START=${MIGRATE_ON_START})"
fi

# Execute the main command (uvicorn)
exec "$@"