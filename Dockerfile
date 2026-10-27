# syntax=docker/dockerfile:1

# ghcr.io/ang-ee/django-angee-base — the framework runtime base image.
#
# Lean and direct on python-slim + uv (no intermediate "docker-django" base): the
# framework's dependency closure is baked into a venv OUTSIDE the app root
# (`/opt/.venv`), so a project's source can bind-mount at its configured root while the
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
# `docker stop` shuts the ASGI server down cleanly. git: the composed stack's
# addon dependency group pulls the pinned ang-ee/strawberry fork — git is needed
# both to bake that closure and for the mounted-source `uv sync` at container
# start. No compiler, no node here (Vite is a separate image): the framework's
# wheels and the composed addon closure both resolve to manylinux binaries, and
# the only sdist left in that closure (pyaes, pure Python) builds without one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 tini ca-certificates git gosu \
    && rm -rf /var/lib/apt/lists/*
# Non-root runtime user; /app and the venv are user-owned so a mounted-source
# `uv sync` at container start can link the editable project into /opt/.venv.
RUN useradd --create-home --uid 1000 angee \
    && install -d -o angee -g angee /app /opt/.venv
COPY docker/runtime-entrypoint.sh /usr/local/bin/angee-django-entrypoint
RUN chmod +x /usr/local/bin/angee-django-entrypoint
WORKDIR /app

# --- deps: bake the dependency closure (git comes from base) --------------------
FROM base AS deps
# `--build-arg DEV=--no-dev` for a runtime-lean image; default includes the dev
# group so a dev workspace can run pytest/ruff/mypy in-container out of the box.
ARG DEV=
COPY pyproject.toml uv.lock ./
# The Strawberry fork projected from the GraphQL addon's manifest is public, so
# uv clones it over anonymous HTTPS — no credential, no SSH. Deps only — the
# project is not built here, so the source tree / hatch-angee are not needed and
# the layer caches until the lock moves.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project ${DEV} \
    && chown -R angee:angee /opt/.venv

# --- final: the lean base + the baked venv (git inherited for the dev uv sync) --
FROM base AS final
# The dev image's contract is a mounted-source `uv sync` of the composed host's
# FULL addon closure at container start. That closure is wheel-only (see the
# base stage note), so this target stays as compiler-free as `base` — the
# build-essential/cmake layer that the Matrix bridge's python-olm once needed is
# gone with it.
COPY --from=deps --chown=angee:angee /opt/.venv /opt/.venv
USER root
ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]
# The stack service supplies the concrete command; a mounted-source dev service
# links the editable project first, e.g.:
#   uv sync --frozen && exec uv run python manage.py runserver 0.0.0.0:8000
CMD ["python", "-c", "import django, sys; print('django-angee-base ready — python', sys.version.split()[0], '· django', django.get_version())"]

# --- runtime: core wheel + native folder-addon sources --------------------------
FROM deps AS runtime
WORKDIR /opt/angee-src
COPY --chown=angee:angee pyproject.toml uv.lock README.md LICENSE LICENSE.GPL ./
COPY --chown=angee:angee angee ./angee
COPY --chown=angee:angee addons ./addons
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra postgres \
    && chown -R angee:angee /opt/.venv
# The wheel owns only core. Native yamlconf adds the baked addon root alongside
# project-owned roots before manifest bootstrap and Django app discovery.
COPY docker/addon-settings.yaml ./addon-settings.yaml
ENV YAMLCONF_CONFFILE=/opt/angee-src/addon-settings.yaml
USER root
WORKDIR /app
ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]

# --- angee-web: native framework package and addon-web workspace ----------------
# Keep the same source/project paths as the Python image: composer-emitted
# relative addon roots then resolve identically during codegen in either image.
FROM node:22-slim AS angee-web
RUN corepack enable
WORKDIR /opt/angee-src
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages ./packages
COPY addons ./addons
RUN pnpm install --frozen-lockfile
