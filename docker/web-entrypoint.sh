#!/bin/sh
set -eu

# The frontend build writes into the project bind mount (codegen output, dist/, the
# @angee/* node_modules links). Adopt the mount owner's uid — the same mapping the
# django entrypoint makes — so those outputs stay host-owned instead of root-owned,
# and the two containers agree on who owns the shared project tree.
if [ "$(id -u)" = "0" ] && [ -d /opt/angee-web/project ]; then
  project_uid="$(stat -c %u /opt/angee-web/project)"
  node_uid="$(id -u node)"
  if [ "$project_uid" != "0" ] && [ "$project_uid" != "$node_uid" ]; then
    usermod -o -u "$project_uid" node
    chown -R node:node /home/node
    # The baked workspace, which the build rewrites (workspace globs, the src/
    # overlay, the pnpm store). NOT the project mount: it is already host-owned,
    # and a recursive chown over it would be both slow and destructive.
    chown -R node:node /opt/angee-web/packages /opt/angee-web/node_modules
    chown node:node /opt/angee-web/package.json /opt/angee-web/pnpm-workspace.yaml \
      /opt/angee-web/pnpm-lock.yaml /opt/angee-web/.npmrc
  fi
  exec gosu node "$@"
fi

exec "$@"
