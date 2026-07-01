# syntax=docker/dockerfile:1

# ghcr.io/ang-ee/django-angee-base — the framework runtime base image.
#
# Lean and direct on python-slim + uv (no intermediate "docker-django" base): the
# framework's dependency closure is baked into a venv OUTSIDE the app root
# (`/opt/.venv`), so a project's source can bind-mount over `/app` in dev while the
# baked deps survive — change code, it's live; change a lockfile, `up --build`
# rebuilds the deps layer. The framework CODE is NOT baked here: it is mounted
# (framework dev) or added by a derived image / installed as the wheel (downstream
# project). One artifact, the container analogue of the `django-angee` wheel.

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
# `docker stop` shuts the ASGI server down cleanly. Nothing else — the framework's
# wheels are self-contained (cryptography/uvicorn[standard]/httptools ship
# manylinux binaries); no compiler, no node (Vite is a separate image).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Non-root runtime user; /app and the venv are user-owned so a mounted-source
# `uv sync` at container start can link the editable project into /opt/.venv.
RUN useradd --create-home --uid 1000 angee \
    && install -d -o angee -g angee /app /opt/.venv
WORKDIR /app

# --- deps: bake the dependency closure; git toolchain lives ONLY in this stage -
FROM base AS deps
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -m 0700 /root/.ssh \
    && ssh-keyscan github.com > /root/.ssh/known_hosts 2>/dev/null
# `--build-arg DEV=--no-dev` for a runtime-lean image; default includes the dev
# group so a dev workspace can run pytest/ruff/mypy in-container out of the box.
ARG DEV=
COPY pyproject.toml uv.lock ./
# Angee pins the ang-ee strawberry forks over ssh (`[tool.uv.sources]`); the SSH
# mount forwards the builder's agent so `uv sync` can clone them. Deps only — the
# project is not built here (`--no-install-project`), so the source tree and the
# hatch-angee build backend are not needed and the layer caches until the lock moves.
RUN --mount=type=ssh --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project ${DEV} \
    && chown -R angee:angee /opt/.venv

# --- final: the lean base + the baked venv, none of the build toolchain --------
FROM base AS final
COPY --from=deps --chown=angee:angee /opt/.venv /opt/.venv
USER angee
ENTRYPOINT ["tini", "--"]
# The stack service supplies the concrete command; a mounted-source dev service
# links the editable project first, e.g.:
#   uv sync --frozen && exec uv run python manage.py runserver 0.0.0.0:8000
CMD ["python", "-c", "import django, sys; print('django-angee-base ready — python', sys.version.split()[0], '· django', django.get_version())"]
