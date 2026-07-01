#!/usr/bin/env bash
# Dev entrypoint for the containerized Vite service. Node is stock; the JS
# workspace is installed from the bind-mounted worktree, then codegen derives the
# typed GraphQL operations from the SDL the app service emitted (shared through
# the same /app mount) and Vite serves with HMR.
set -euo pipefail
cd /app
corepack enable >/dev/null 2>&1 || true

WEB="${ANGEE_WEB_DIR:?ANGEE_WEB_DIR must point at the web package}"

echo "==> pnpm install (workspace)"
pnpm install

echo "==> codegen (typed GraphQL ops from the app's emitted SDL)"
pnpm --dir "$WEB" codegen || echo "   (codegen deferred — retries once the app has emitted schema)"

echo "==> vite dev on 0.0.0.0:${ANGEE_UI_PORT:-5173}"
exec pnpm --dir "$WEB" dev --host 0.0.0.0 --port "${ANGEE_UI_PORT:-5173}"
