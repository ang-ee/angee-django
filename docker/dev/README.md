# Containerized dev stack (M1 reference)

A hand-written docker-compose that runs the **notes-angee** example fully
containerized — Django + Vite over `ghcr.io/ang-ee/django-angee-base`, with the
worktree bind-mounted at `/app` (deps baked into the image's venv, code live).

This is the **shape the operator's `stacks/dev` container variant generates** from
`angee.yaml`; kept here as a reference you can run directly to exercise the base
image + mount pattern without the operator.

```sh
docker compose -f docker/dev/docker-compose.yml up --build
#   app  → http://localhost:8000     vite → http://localhost:5173
```

- **`Dockerfile`** (repo root) builds `django-angee-base`: `python:3.14-slim` + uv,
  the framework's dependency closure baked into `/opt/.venv` **outside** `/app`, so
  the mount overlays the code while deps survive. `up --build` on a lockfile change
  rebuilds the deps layer. The `build.ssh: [default]` forwards an agent so the
  private `ang-ee/strawberry*` forks clone.
- **`app-entrypoint.sh`** links the editable framework from the mount (`uv sync`),
  emits the runtime (`angee build`), migrates, syncs REBAC, emits SDL, and serves
  with uvicorn.
- **`vite-entrypoint.sh`** installs the JS workspace and serves Vite with HMR.

**SQLite for now.** Postgres (pgvector, bind data, a leased port) is the opt-in
that lands with `psycopg` + the RAG work — see the operator's
`local-platform-instance` / `operator-backup-restore` proposals. Compose project
isolation for parallel workspaces is owned by the operator (`composeProjectName`);
this single-stack reference doesn't need it.
