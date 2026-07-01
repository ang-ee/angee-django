#!/usr/bin/env bash
# Dev entrypoint for the containerized Django app service. The framework's deps
# are baked into the image's venv (/opt/.venv); the worktree is bind-mounted at
# /app, so this links the framework editable from the mount, emits the runtime,
# migrates, and serves. Dependency changes are picked up by `up --build` (the
# image's deps layer) — this only reconciles the editable project link.
set -euo pipefail
cd /app

PROJECT="${ANGEE_PROJECT_DIR:?ANGEE_PROJECT_DIR must point at the project dir}"
MANAGE="uv run python ${PROJECT}/manage.py"

echo "==> uv sync (link the editable framework from the mount)"
uv sync --frozen

echo "==> angee build (emit the runtime from source models)"
$MANAGE angee build

echo "==> makemigrations + migrate"
$MANAGE makemigrations
$MANAGE migrate --noinput

echo "==> rebac sync (permissions)"
$MANAGE rebac sync --yes || echo "   (rebac sync skipped/failed — non-fatal for a first boot)"

echo "==> schema (emit SDL for the frontend codegen)"
$MANAGE schema || true

echo "==> runserver on 0.0.0.0:${ANGEE_DJANGO_PORT:-8000}"
exec $MANAGE runserver "0.0.0.0:${ANGEE_DJANGO_PORT:-8000}"
