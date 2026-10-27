#!/bin/sh
set -eu

project_dir=${ANGEE_PROJECT_DIR:-/app}

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$project_dir/runtime" "$project_dir/data"
  chown -R angee:angee "$project_dir/runtime"
  chown angee:angee "$project_dir/data"
  exec gosu angee "$@"
fi

exec "$@"
