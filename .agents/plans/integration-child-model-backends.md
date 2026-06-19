# Integration Child Models And Backend Classes

## Goal

Rework integration implementations so `integrate.Integration` is the shared
multi-table inheritance parent connection row and concrete integration kinds are
Django child models. A child model owns the fields, actions, tabs, and related
tables for that kind. If several adapters share one child shape, that child
carries a role-named `backend_class` `ImplClassField`.

Status for the clean MTI slice: composer MTI support, `VcsBridge`, and
`InferenceProvider` are implemented. OpenAI/Anthropic/manual/OpenAI-compatible
providers are inference `backend_class` values, not integration impls.

## Next Cleanup Queue

- [x] Extract shared `ImplClassField` update/default plumbing. `VcsBridge` and
  `InferenceProvider` currently duplicate the same GraphQL sequence: normalize
  backend key, detect changes, track provided fields, materialize defaults, and
  save. The owner should be the impl/defaults layer (`angee.base.impl` /
  `angee.base.fields`) so schema resolvers stay thin.
- [ ] Consider an omission sentinel convention for `ImplDefaultsMixin.impl_key_for`
  if a third caller needs create-time default key handling. Current callers pass
  `None` for omitted GraphQL values explicitly; avoid importing GraphQL sentinels
  into the base layer.
- [x] Add a base record-action helper for single-id `ActionResult` mutations.
  Integrate pages repeatedly guard `ctx.record.id`, call `useActionMutation`,
  refresh the record, and return the message. The owner should be `@angee/base`
  because it owns `ActionContext` and record action UI; pages should compose one
  hook per action. Implemented as `useRecordAction` /
  `useRecordActionMutation` in `@angee/base`, then wired into agents and
  integrate record actions.
- [ ] Make relation facets/group options a base/DataPage primitive before
  hand-authoring more inference list filters. The provider/model-capability
  filters should reuse relation metadata and the same relation option-fetch path
  as fields, not page-local list queries.
- [ ] Standardize relation-id filters. Prefer a strawberry-django-native
  relation filter shape from the frontend primitive; if not enough, add one
  relay/global-id relation lookup helper in `angee.graphql` and reuse it across
  agents/knowledge.

## Locked Decisions

- Use Django multi-table inheritance as the model shape for integration kinds
  whenever a concrete model is needed. `Integration` is the parent;
  `InferenceProvider`, `VcsBridge`, and future mutually exclusive integration
  kinds are child rows.
- Do not use `Integration.impl_class` or `IntegrationImpl.related_model` to
  decide which child row exists. That was manual polymorphism and is superseded
  by Django model inheritance.
- Keep `ImplClassField`, but never let it create companion rows. A concrete
  owner may use a role-named field such as `InferenceProvider.backend_class` or
  `VcsBridge.backend_class`. `Integration.impl_class` may remain only for
  parent-level adapter behavior that genuinely belongs to the shared
  integration identity.
- Backend impl classes are default-bearing and inheritable. For example an
  OpenAI-compatible inference backend base can provide shared protocol/default
  behavior, while `openai`, `deepseek`, and similar keys specialize labels,
  endpoints, model filters, and defaults.
- Use model `extends` only for additive same-row fields contributed by a
  downstream addon. OIDC login fields on `integrate.OAuthClient` are the
  canonical shape. If exactly one concrete kind applies, use a concrete Django
  child model instead.
- Catalogue rows stay pure metadata. They do not choose implementation behavior;
  the concrete integration child row does.

## Dependency Spike

**Status:** rejected for this slice after spike on 2026-06-19.

The spike tested `django-polymorphic` as the parent-query and downcasting owner.
It should not be added to `docs/stack.md`, `pyproject.toml`, or `uv.lock` for
the integration child-model refactor.

Evidence:

- `django-polymorphic` does not compose as a drop-in owner with Angee's current
  REBAC model bases. The spike only got past import by adding local
  REBAC/polymorphic queryset-manager glue and a custom metaclass bridge.
- The first spike also exposed a composer blocker: materialized children could
  not yet inherit the generated runtime parent across app labels. That blocker
  is now fixed by the composer support for `extends + runtime = True`.
- With the composer blocker removed, the dependency still does not buy enough:
  accepting it would reintroduce REBAC/polymorphic manager glue before native
  Django MTI has been tried on the real integration models.

Accept `django-polymorphic` only if it reduces local code and keeps these
surfaces boring:

- `Integration.objects` can list common connection rows while detail routes and
  actions can receive concrete child instances without a local downcasting
  registry.
- REBAC managers and field-backed relations continue to scope parent and child
  querysets fail-closed.
- strawberry-django can expose parent lists and child detail/types without
  custom type-resolution glue that outweighs the dependency.
- resources can import concrete child rows deterministically.
- aggregate/list/group queries over `Integration` still work for status, vendor,
  owner, kind, and child-type grouping.

The composer prerequisite is now implemented: `extends = "app.Model"` plus
`runtime = True` emits a materialized Django multi-table-inheritance child whose
generated concrete class inherits the target's generated runtime model and the
child source model. Reopen `django-polymorphic` only if native Django MTI plus a
small owner-level resolution seam proves insufficient after real integration
models are converted.

Smallest accepted parent-to-child alternative: keep parent list/group/aggregate
fields owned by `Integration`, keep concrete behavior on the child owner, and add
one tiny Angee-owned resolution seam on the owner object such as
`integration.kind_row()` / `integration.provider` instead of caller-side shape
inspection.

## Architecture Gate

Owner map:

- `integrate.Integration` owns common connection identity, owner, vendor,
  credential/account, status, telemetry, list/group surfaces, connect/attach
  lifecycle, and parent-query behavior.
- Concrete integration child models own mutually exclusive integration kinds:
  `agents.InferenceProvider` owns inference provider fields, model refresh, chat,
  service environment, model catalogue tabs/actions; `integrate.VcsBridge` owns
  VCS bridge fields, sync state, webhook verification, repository discovery.
- Child-model `backend_class` fields own interchangeable SDK/protocol adapters
  within one concrete kind, for example OpenAI/Anthropic/manual inference
  backends on `InferenceProvider` and local/GitHub VCS backends on `VcsBridge`.
- `extends = "app.Model"` remains additive only: downstream fields on the same
  row, such as OIDC login fields on `OAuthClient`.
- Resources own shipped seed data. They should seed concrete child rows once the
  child model owns the implementation kind.
- React form slots/tabs own presentation. They render one Integration form and
  reveal tabs/fields based on the concrete child kind and backend metadata.

Sibling inventory:

- `iam_integrate_oidc` uses `extends = "integrate.OAuthClient"` for additive
  fields on the same OAuth client row. Keep this pattern.
- `storage.Backend.backend_class` uses `ImplClassField` for behavior over a
  common persisted shape. This is the model for `InferenceProvider.backend_class`
  and `VcsBridge.backend_class`.
- Deleted `IntegrationImpl.related_model` manually created one-to-one related
  rows (`InferenceProvider`, `VcsBridge`). Keep that path gone.

Dependency check:

- Django multi-table inheritance is the accepted model shape and needs no new
  dependency.
- `django-polymorphic` was rejected for this slice by the 2026-06-19 spike. Do
  not add it to `docs/stack.md`, `pyproject.toml`, or `uv.lock`.
- Do not build a local downcasting registry. If resolving from parent to
  concrete owner is needed before true MTI is implemented, put one small method
  on the owner object instead.

Naming check:

- The child model names the integration kind. Adapter selection inside the child
  is named by role (`backend_class`, `provider_type`, etc.). Do not use an
  implementation key as a hidden child-row discriminator.

## Desired Shape

```python
class Integration(...):
    owner = ...
    vendor = ...
    credential = ...
    account = ...
    status = ...
    # no generic config bucket


class InferenceProvider(Integration):
    backend_class = ImplClassField(
        base_class=InferenceBackend,
        registry_setting="ANGEE_INFERENCE_BACKEND_CLASSES",
        default="manual",
    )
    name = ...
    base_url = ...
    credential_env = ...
    config = ...


class VcsBridge(Integration):
    backend_class = ImplClassField(
        base_class=VCSBackend,
        registry_setting="ANGEE_VCS_BACKEND_CLASSES",
        default="local",
    )
    webhook_secret = ...
    config = ...
    cursor = ...


class OpenAICompatibleInferenceBackend(InferenceBackend):
    defaults = {
        "config": {"api_protocol": "openai_responses"},
    }


class OpenAIInferenceBackend(OpenAICompatibleInferenceBackend):
    label = "OpenAI"
    defaults = {"base_url": "https://api.openai.com/v1"}


class DeepSeekInferenceBackend(OpenAICompatibleInferenceBackend):
    label = "DeepSeek"
    defaults = {"base_url": "https://api.deepseek.com/v1"}
```

## Implementation Plan

1. Lock the child-model contract for integrations.
   - [x] Teach the composer that `extends = "app.Model"` plus `runtime = True`
     emits a materialized Django multi-table-inheritance child.
   - [x] Make `Integration` the Django multi-table inheritance parent model for
     the first real child, `integrate.VcsBridge`.
   - [x] Apply the same parent/child shape to `agents.InferenceProvider`.
   - [x] Use the 2026-06-19 `django-polymorphic` spike result: reject the dependency
     for this slice and try native Django MTI first.
   - Keep parent queries stable for existing list/group/permission surfaces.
   - Verify base-list to child-detail resolution uses the chosen owner rather
     than ad hoc caller-side shape inspection.

2. Move inference implementation selection.
   - [x] Add `InferenceProvider.backend_class`.
   - [x] Move OpenAI/Anthropic/manual registry entries from unified
     `ANGEE_INTEGRATION_IMPLS` to an inference-specific registry.
   - [x] Move `InferenceBackend` behavior so it binds to `InferenceProvider` directly,
     not to `Integration + related`.

3. Convert `InferenceProvider` from one-to-one related model to child model.
   - [x] Preserve common `Integration` fields on the parent.
   - [x] Preserve inference fields and methods on the child.
   - [x] Make model refresh/chat/service env ask the child owner directly.

4. Convert VCS bridge to the same shape.
   - [x] `VcsBridge` becomes an `Integration` child.
   - [x] Move VCS backend selection to role-named `VcsBridge.backend_class`.
   - [x] Split `Bridge` away from the manual `IntegrationMixin` one-to-one base.
   - [x] Repository/source/template relations keep pointing at the concrete
     bridge owner.
   - [x] Move VCS options (`local_root`, `github_org`, backend test data) to
     child-owned `VcsBridge.config`.

5. Collapse setup UX into one Integration form.
   - [x] Parent integration list remains the navigation surface.
   - [x] VCS and inference child pages create direct child rows.
   - [ ] Follow up: collapse child-specific setup into one parent-detail form
     only if it deletes frontend route/form code.

6. Update resources and demo seeds.
   - [x] Seed concrete child model rows instead of an integration row plus related row.
   - [x] Keep catalogue model resources pure metadata.
   - [x] Regenerate runtime/schema artifacts from source during verification.

7. Delete obsolete glue.
   - [x] Remove `IntegrationImpl.related_model`, related-row creation helpers, and
     duplicated GraphQL create mutations once child creation owns the flow.
   - [x] Remove `Integration.config`, `set_credential_env()`, and
     `credential_env_value()`.
   - [x] Remove `IntegrationMixin`.
   - [ ] Remove child CRUD pages only after a shared parent-detail composition
     deletes code and preserves list/group/board affordances.

## Remaining Follow-Ups

- Native parent list rows still need a tiny owner-level route from a generic
  `Integration` to its concrete child when the UI opens a row from the parent
  list. Add that only when the frontend detail composition needs it.
- Reopen `django-polymorphic` only if native Django MTI plus that small owner
  seam grows more code than the dependency would delete.
- Do not add migrations or compatibility shims for the pre-1.0 database; runtime
  and DB are disposable for this refactor.

## Verification

- [x] `uv run examples/notes-angee/manage.py angee build`
- [x] Fresh generated runtime migrations + `uv run examples/notes-angee/manage.py migrate`
- [x] `uv run examples/notes-angee/manage.py rebac sync`
- [x] `uv run examples/notes-angee/manage.py resources load`
- [x] `uv run examples/notes-angee/manage.py resources load --include-demo`
- [x] `uv run examples/notes-angee/manage.py check`
- [x] `uv run examples/notes-angee/manage.py schema --check`
- [x] `uv run pytest tests addons/angee/resources/tests`
- [x] `uv run ruff check $(git diff --name-only -- '*.py')`
- [ ] Browser check that creating an inference integration happens through one
  form and no separate provider form is required; this remains tied to the
  follow-up parent-detail composition.
