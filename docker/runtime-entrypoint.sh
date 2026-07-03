#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/runtime /app/.angee/data
  chown -R angee:angee /app/runtime
  chown angee:angee /app/.angee/data
  exec gosu angee "$@"
fi

exec "$@"
