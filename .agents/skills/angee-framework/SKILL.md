---
name: angee-framework
description: "Change Angee shared primitives, package boundaries, dependencies or Copier contracts. Routes base and server policy to addon owners and generic composition to django-compose."
---

# Angee Framework

Use this for a shared framework seam, dependency change, package boundary or
Copier contract. Run `angee-design` first. Product behavior stays with its addon;
use the native compiler owner for generic Django composition.

## Owners

| Concern | Owner |
|---|---|
| Settings/deployment overlay | `addons/angee/base/conf/__init__.py` and `conf/yamlconf.py` |
| Angee defaults and native compiler integration selection | `addons/angee/base/autoconfig.py` |
| Package capability facts and conventional contributions | `addons/angee/base/addons.py` |
| Shared callables, paths, serialization and model primitives | `addons/angee/base/` |
| Generic closure, declarations, build, receipt, migration and launcher | Installed `django_compose` public modules |
| Database preparation and readiness | `addons/angee/base/provision.py` |
| Optional HTTP policy and Uvicorn commands | `addons/angee/server/` |
| REBAC, resource and web projections | Their owning addon `build.py` integrations |
| Native package layout | `tools/hatch-angee/` over Hatchling |
| React package boundaries | `packages/app/src/architecture-guardrails.test.ts` |
| Project/stack/addon source | `templates/`; edit source and its contract tests |

## Work

1. Name the missing behavior and read its owner plus two existing callers. Use
   [the stack](../../../docs/stack.md) to find the upstream native API. Extend
   that owner; remove the replaced implementation and migrate callers.
2. Put reusable model/field/queryset behavior in `angee.base`, HTTP policy in
   `angee.server`, and capability-specific behavior in its addon. A generic
   compiler defect belongs upstream, with no Angee/product vocabulary.
3. Keep serving modules independent of build adapters. Addon `build.py` classes
   implement `django_compose.declarations` protocols and are selected by dotted
   `COMPOSE_INTEGRATIONS` entries. Their outputs share native publication and
   verification, rather than a private generated-file lifecycle.
4. Use `angee.base.hooks` for callable settings and base's package-data reader
   for capability tables. Closure and identity come from the native installed
   addon index. Do not introduce a parallel discovery cache or loader.
5. Change dependencies at the owning package's `pyproject.toml` or JS manifest,
   update the concern row in `docs/stack.md`, and regenerate the native lock at
   its owner. Root releases select the framework cohort; fixtures, examples and
   the backend retain independent versions.
6. A template change preserves Copier answers and relative-path semantics across
   project, stack and workspace templates. Shared Python runtime settings live
   in `templates/stacks/_shared/python-runtime.yaml.jinja`; lifecycle sequencing
   belongs to the rendered operator job graph.

## Verify

Use [Checks](../../../docs/checks.md) for the execution root and appropriate
Python/template/package checks. Complete source and caller migrations before
consolidated verification. Reuse passing evidence for unchanged code. Shared
foundation changes need the full relevant integrated Python suite; meaningful UI
changes need browser evidence. Missing integration or dependencies are explicit
handoff boundaries, never reasons to weaken checks.

## Read next

- [Ownership and layering](../../../docs/backend/guidelines.md#package-layering)
- [Composition flow](../../../docs/composer.md#flow)
- [Web build pitfalls](references/web-pitfalls.md)
