---
name: angee-project
description: "Configures a customer system's host: stack root vs project vs workspace, angee.yaml profiles and include_* inputs, pyproject.toml, settings.yaml (INSTALLED_APPS, native uv sources, yamlconf, DATABASE_URL), secrets, first provision. Use for 'new project', 'enable addon', 'stack settings'."
---

# Angee Project

Use for a project's authored settings, root apps, native requirements, host web
package or stack profile. Addon-local declarations belong to `angee-addon`.

## Owners

| Fact | Owner |
|---|---|
| Installed distributions and local sources | Project `pyproject.toml` and `uv.lock` |
| Root apps and structural output identity | Project `settings.yaml` / `settings.py` |
| Deployment YAML, `.env`, typed service URLs | `angee.base.conf.configure` |
| Closure, ordered addon settings, build/serving mode | `django_compose.conf.configure` |
| Management dispatch | `django_compose.management.execute` |
| Database hooks and readiness | `django_compose.provision` |
| Dependency chain and service holds | Controlling stack `angee.yaml` |
| Fragment install membership | Stack `pnpm-workspace.yaml` |
| Codegen and generated app entry | Host `web/package.json` and `@angee/app` CLI |

## Work

1. Resolve the controlling stack with `angee-workspace`; never initialize a stack
   under a source checkout. Read its rendered manifest before choosing a job.
2. Add native requirements and portable uv source overrides at the project owner.
   Local addons are real installed distributions; folder paths do not activate
   them. Keep workspace member globs aligned with actual packages. Compiler
   requirements use the committed immutable public Git source; optional local
   co-development belongs in a separate disposable check environment, never a
   live stack or a committed second resolution surface.
3. Declare root apps in `INSTALLED_APPS`. Requirements activate their required
   addon providers; foreign Django app edges come from `AppConfig.requires_apps`.
   Keep `COMPOSE_OUTPUT_DIR` and `COMPOSE_OUTPUT_MODULE` authored and importable.
4. The rendered `settings.py` calls `angee.base.conf.configure(globals())`.
   `manage.py` selects that authored settings module and calls the native launcher.
   Addon `autoconfig` contributes settings, not a second project bootstrap.
5. Deployment URLs and secrets belong in the stack's environment or explicit
   YAML overlay. Native yamlconf provenance must survive expansion. Do not move
   structural app/output identity into environment-derived values.
6. Use native workspace/package membership for web fragments. The web projector
   emits the manifest; the host's `angee-web-codegen` pass consumes emitted SDL.
   Add a pnpm member for an external fragment rather than copying it into the host.
7. On a running stack, invoke its whole chained dependency job through the
   operator. Current framework templates use `deps`; instance templates use
   `py-sync`. Sync holds Django/workers, then compose and provision run before
   successful recovery. A plain held job intentionally leaves services stopped.
   Never synchronize the running stack's venv by hand.
8. Package install/removal uses the same `py-sync` job with native optional
   inputs and chained restart. The platform preflight owns native uv planning and
   migration-import auditing; the generic operator only owns job execution.

## Verify

Use [Composition and schema](../../../docs/checks.md#composition-and-schema)
and [restart guidance](../../../docs/howto/getstarted.md#restart-the-running-stack).
Verify authored roots, native installed metadata, generated artifacts, provision
readiness, and web codegen at their separate owners. Do not claim a build or an
editable install has provisioned a database. Preserve comments and Copier answers
when editing existing project declarations.

`ANGEE_DB_POOL` is for production Django workers; database URLs with
`CONN_MAX_AGE` conflict with native pooling. Celery and management jobs retain
short-lived connections. The shared Python runtime include owns the process
`.venv` or Docker shared `data/.venv-linux` selector.
