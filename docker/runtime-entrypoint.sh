#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
  # The image bakes uid 1000, but /app is a host bind mount whose owner is whoever
  # runs the stack (501 on macOS, an arbitrary uid on a server). Adopt that uid so
  # everything this container writes into the mount stays host-owned and writable —
  # both ways: the container can create its own paths, and the host user can still
  # remove them. `-o` allows a non-unique uid; the venv keeps group rights (g=u).
  app_uid="$(stat -c %u /app)"
  angee_uid="$(id -u angee)"
  if [ "$app_uid" != "0" ] && [ "$app_uid" != "$angee_uid" ]; then
    usermod -o -u "$app_uid" angee
    chown -R angee:angee /home/angee
  fi
  # Outputs the stack expects inside the mount: the composer's runtime/, the data
  # dir, and the stack-owned uv cache (UV_CACHE_DIR=caches/uv, relative to /app).
  # A bind mount arrives owned by the host, so these must exist before the drop.
  mkdir -p /app/runtime /app/.angee/data /app/caches/uv
  chown -R angee:angee /app/runtime
  chown angee:angee /app/.angee/data
  chown angee:angee /app/caches/uv
  exec gosu angee "$@"
fi

exec "$@"
