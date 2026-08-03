# syntax=docker/dockerfile:1

# ghcr.io/ang-ee/django-angee-base — the framework runtime base image.
#
# Lean and direct on python-slim + uv (no intermediate "docker-django" base): the
# framework's dependency closure is baked into a venv OUTSIDE the app root
# (`/opt/.venv`), so a project's source can bind-mount over `/app` in dev while the
# baked deps survive — change code, it's live; change a lockfile, `up --build`
# rebuilds the deps layer. The framework CODE is NOT baked here: it is mounted
# (framework dev) or added by a derived image / installed as the wheel (downstream
# project). Two targets share these stages: `final` is the deps-only base image
# (`django-angee-base`, the container analogue of the `django-angee` wheel's deps);
# the `runtime` target below is the derived image this comment foretells — it bakes
# the framework code + `[postgres]` into `ghcr.io/ang-ee/django-angee`, the fuller
# image the self-contained `local` stack runs.

ARG PYTHON_VERSION=3.14

# --- base: the lean OS+python layer, shared by `deps` and `final` --------------
FROM python:${PYTHON_VERSION}-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Build autobahn (a transitive of the test-only daphne) pure-Python: its NVX
    # CFFI extension needs a compiler we deliberately omit, and Angee serves with
    # uvicorn — daphne is only for channels' test live-server. Keeps the base
    # compiler-free.
    AUTOBAHN_USE_NVX=0
# libmagic1: python-magic (storage MIME sniffing). tini: PID 1 signal reaping so
# `docker stop` shuts the ASGI server down cleanly. git: uv resolves the pinned
# ang-ee/strawberry* git deps with it — needed both to bake the closure (deps) and
# for the mounted-source `uv sync` at container start (the dev flow this image
# exists for). No compiler, no node (Vite is a separate image); the framework's own
# wheels ship manylinux binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 tini ca-certificates git gosu \
    && rm -rf /var/lib/apt/lists/*
# Non-root runtime user; /app and the venv are user-owned so a mounted-source
# `uv sync` at container start can link the editable project into /opt/.venv.
RUN useradd --create-home --uid 1000 angee \
    && install -d -o angee -g angee /app /opt/.venv
WORKDIR /app

# --- deps: bake the dependency closure (git comes from base) --------------------
FROM base AS deps
# The toolchain lives in THIS stage only. Not every locked dependency ships a wheel
# for the supported Python on every architecture (audioop-lts arrives as an sdist,
# python-olm always builds its vendored libolm), so the closure cannot be baked
# without a compiler — but the images that serve must stay compiler-free, and they
# do: every stage below starts again from `base` and copies the finished venv.
# clang + cmake are the toolchain `[tool.uv.extra-build-variables]` declares for
# python-olm; the declared variables are part of uv's cache key, so the wheel built
# here is reused by a mounted-source `uv sync` instead of being rebuilt.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential clang cmake \
    && rm -rf /var/lib/apt/lists/*
# `--build-arg DEV=--no-dev` for a runtime-lean image; default includes the dev
# group so a dev workspace can run pytest/ruff/mypy in-container out of the box.
ARG DEV=
COPY pyproject.toml uv.lock ./
# The ang-ee/strawberry* forks pinned in `[tool.uv.sources]` are public, so uv
# clones them over anonymous HTTPS — no credential, no SSH. Deps only — the project
# is not built here, so the source tree / hatch-angee are not needed and the layer
# caches until the lock moves. `g=u` mirrors owner rights onto the angee group so
# the venv stays writable after the entrypoint remaps the user to the bind-mount
# owner's uid (the gid stays angee's).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project ${DEV} \
    && chown -R angee:angee /opt/.venv \
    && chmod -R g=u /opt/.venv

# --- runtime-base: the lean base + the baked venv + the entrypoint --------------
# Shared by `final` and `runtime`. The entrypoint is copied AFTER the expensive
# dependency layer on purpose: editing bootstrap/permission behaviour must not
# invalidate the dependency build.
FROM base AS runtime-base
COPY --from=deps --chown=angee:angee /opt/.venv /opt/.venv
COPY docker/runtime-entrypoint.sh /usr/local/bin/angee-django-entrypoint
RUN chmod +x /usr/local/bin/angee-django-entrypoint

# --- final: the lean runtime base (git inherited for the dev uv sync) -----------
FROM runtime-base AS final
USER root
ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]
# The stack service supplies the concrete command; a mounted-source dev service
# links the editable project first, e.g.:
#   uv sync --frozen && exec uv run python manage.py runserver 0.0.0.0:8000
CMD ["python", "-c", "import django, sys; print('django-angee-base ready — python', sys.version.split()[0], '· django', django.get_version())"]

# --- runtime: runtime-base + framework code + [postgres] ------------------------
# ghcr.io/ang-ee/django-angee — the FULL framework runtime image (framework CODE +
# deps + psycopg baked), distinct from the deps-only `final`/django-angee-base
# above. The self-contained `local` stack runs its `django` service on this image:
# the project bind-mounts over /app while `import angee.*` resolves from the baked
# wheel in /opt/.venv, independent of the mount. Build it explicitly:
#   docker build --target runtime -t ghcr.io/ang-ee/django-angee:latest .
FROM runtime-base AS runtime
# Off /app on purpose: the local stack bind-mounts the project at /app, so the
# framework source lands at /opt/angee-src — a mount over /app can never hide it.
WORKDIR /opt/angee-src
# The forks in [tool.uv.sources] are public (git inherited from base clones them
# over anonymous HTTPS → credential-free). README.md + both license files satisfy
# the wheel build's metadata (`license-files`: LGPLv3 additional permissions plus
# the GPLv3 they extend); angee + addons are the two source roots the hatch-angee
# backend merges onto the one `angee.*` PEP 420 namespace.
COPY --chown=angee:angee pyproject.toml uv.lock README.md LICENSE LICENSE.GPL ./
COPY --chown=angee:angee angee ./angee
COPY --chown=angee:angee addons ./addons
# --no-editable installs the framework as a BUILT wheel into /opt/.venv (via the
# hatch-angee backend) so `import angee.*` resolves from site-packages regardless
# of the /app project mount; --extra postgres pulls psycopg (the driver Django 6's
# django.db.backends.postgresql uses); --no-dev drops daphne/pytest/etc. for a lean
# runtime; --frozen honours the committed lock (relocked to carry the extra).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra postgres \
    && chown -R angee:angee /opt/.venv \
    && chmod -R g=u /opt/.venv
USER root
WORKDIR /app
# tini reaps PID 1 for a clean `docker stop`. The stack's django service supplies
# the concrete command (wait-for-postgres · migrate · rebac sync · runserver); the
# venv is on PATH and runtime/ is committed, so there is no uv/angee build at start.
ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]

# --- web-src: collect every @angee/* JS package the wheel ships into one tree -----
# The framework JS is scattered in the venv — the core libs at angee/web/*, each
# addon's frontend at angee/<addon>/web (e.g. @angee/operator, @angee/iam). Flatten
# them into /opt/angee-js/<name> so the web image COPIES one dir as a flat workspace.
FROM runtime AS web-src
USER root
WORKDIR /opt/.venv/lib/python3.14/site-packages
RUN mkdir -p /opt/angee-js && \
    find angee -name package.json -not -path '*/node_modules/*' | while read -r f; do \
      name=$(sed -n 's/.*"name": *"@angee\/\([^"]*\)".*/\1/p' "$f" | head -1); \
      [ -n "$name" ] && cp -R "$(dirname "$f")" "/opt/angee-js/$name"; \
    done

# --- angee-web: node + ALL the framework @angee/* packages as a pnpm workspace ----
# ghcr.io/ang-ee/angee-web — the framework's JS runtime. The @angee/* packages are
# private and ship inside the django-angee wheel (one distribution channel), so this
# image COPIES them (via web-src) straight from the runtime venv — they can never
# drift from the Python side — and installs their deps once into a pnpm workspace. The
# self-contained `local` stack runs its `vite` service here: the project's web/
# bind-mounts in and joins the workspace, resolving @angee/* (whose inter-deps use
# `workspace:*`) from the baked packages. Build it explicitly:
#   docker build --target angee-web -t ghcr.io/ang-ee/angee-web:latest .
FROM node:22-slim AS angee-web
# gosu: the entrypoint drops to the project bind-mount's owner (see below).
# COREPACK_ENABLE_DOWNLOAD_PROMPT=0: a mounted project may pin a different
# `packageManager`, and corepack's interactive download prompt would hang a build
# that has no TTY.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable
WORKDIR /opt/angee-web
# Every @angee/* package (core + addon frontends), byte-identical to the wheel's JS.
COPY --from=web-src /opt/angee-js ./packages
# A workspace over the copied packages so `workspace:*` inter-deps resolve;
# link-workspace-packages so a downstream project's `@angee/*: ^x` range still links
# the baked package by name; auto-install-peers keeps a single React instance.
RUN printf 'packages:\n  - "packages/*"\n' > pnpm-workspace.yaml \
 && printf '{"name":"@angee/web-runtime","private":true,"packageManager":"pnpm@11.1.3"}\n' > package.json \
 && printf 'link-workspace-packages=true\nprefer-workspace-packages=true\nauto-install-peers=true\n' > .npmrc \
 && pnpm install \
 && chown -R node:node /opt/angee-web \
 && chmod -R g=u /opt/angee-web
# This image writes INTO the project bind mount (codegen output, dist/, the
# node_modules links), so it must write as the mount's owner — otherwise the
# outputs land root-owned and neither the host user nor the django container (which
# maps to the same owner) can rewrite them. The entrypoint owns that mapping.
COPY docker/web-entrypoint.sh /usr/local/bin/angee-web-entrypoint
RUN chmod +x /usr/local/bin/angee-web-entrypoint
ENTRYPOINT ["/usr/local/bin/angee-web-entrypoint"]
