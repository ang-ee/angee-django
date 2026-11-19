#!/bin/sh
set -eu

project_dir=${ANGEE_PROJECT_DIR:-/app}

if [ "$(id -u)" = "0" ]; then
  # Run as the owner of the mounted project, as the operator does. A Linux bind
  # mount keeps numeric ids, so the stack root belongs to whoever rendered it,
  # whatever its uid; files are written as that user. A root-owned project (as
  # Docker Desktop may report one) keeps the image's own angee user.
  uid=$(stat -c %u "$project_dir")
  gid=$(stat -c %g "$project_dir")
  if [ "$uid" != "0" ] && [ "$uid" != "$(id -u angee)" ]; then
    if [ "$gid" != "0" ]; then
      groupmod -o -g "$gid" angee
    fi
    usermod -o -u "$uid" angee
  fi
  mkdir -p "$project_dir/runtime" "$project_dir/data"
  chown -R angee:angee "$project_dir/runtime"
  chown angee:angee "$project_dir/data"
  exec gosu angee "$@"
fi

exec "$@"
