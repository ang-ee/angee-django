# Model and query pitfalls

Moved from the backend guide. The contracts stayed there:
[Rules](../../../../docs/backend/guidelines.md#rules), [Persistence primitives](../../../../docs/backend/guidelines.md#persistence-primitives)
and [Direct record access](../../../../docs/backend/guidelines.md#direct-record-access).

- **Group counts add to root totals only for disjoint populations.** Reuse the
  grouped scan with a window total when every record belongs to one bucket;
  recipient fan-out and shared content require distinct root counts. Hydrate
  labels after paging unless they determine group identity or sort order.
- **`.values_list(...).distinct()` must clear the model's default ordering.**
  `Meta.ordering` columns silently join the DISTINCT projection, so a
  single-column `values_list("owner_id").distinct()` returns one row per
  *source row*, not per owner — a loop over it repeats its whole body once per
  row (a 4-hour beat tick that should take a second, live-measured). Append
  `.order_by()` (or order only by the selected columns) before `.distinct()`
  on every values/values_list distinct read.
- **Parties bookkeeping follows its writer's transaction contract.** The
  [persistence contract](../../../../docs/backend/guidelines.md#persistence-primitives)
  owns the rule. Ingest defers its party enrichment with
  `transaction.on_commit`, OIDC login contains its handle claim, and both log
  failures and let the foreign write continue; never let a best-effort
  enrichment roll a foreign write back, and never defer IAM's own person
  linkage ([`person_created`](../../../../addons/angee/iam/events.py) fires before commit).
- **IAM stores the canonical person email.** `bulk_create` and queryset
  `update()` bypass [`UserManager.normalize_email`](../../../../addons/angee/iam/models.py);
  normalize before writing email through them
  ([persistence contract](../../../../docs/backend/guidelines.md#persistence-primitives)).
- **Integration children use the ordinary emitted Django MRO.** The composer
  emits donors, the child's abstract source, then its concrete parent
  ([runtime build and import](../../../../docs/composer.md#runtime-build-and-import)),
  so child behavior can override parent behavior and cooperative methods
  delegate with `super()`. A verb starting from an `Integration` parent row must
  still resolve the concrete child before dispatch because Django does not
  downcast multi-table parent instances automatically
  (`Integration.concrete_capability` composes the shared child lookup). Walking
  `models_with(base=Bridge)` fans a query across every installed bridge table, so it is not
  free.
- **Instance `save()`/`delete()` overrides do not run on cascade or bulk queryset paths.**
  Lifecycle guards and side effects that must survive those paths belong on Django
  signals calling the owning model rules. Agents' teardown and active-turn guards
  use `pre_delete`; service-user deactivation uses `post_delete`. See the
  [agents receivers](../../../../addons/angee/agents/signals.py).
- **Django 6 refreshes `F()`/expression fields back onto the instance via
  `UPDATE ... RETURNING` before `post_save`.** A `save(update_fields=…)` whose
  fields hold expressions (`F("count") + 1`, `Greatest(…)`) leaves the instance
  carrying the DB-true resolved values, not the expression objects — the
  `post_save` receiver (and any `changes` publisher) sees the true row. Never
  "restore" a locally recomputed value (`prior + 1`) after such a save: it stomps
  the RETURNING value and undercounts whenever a concurrent write advanced the
  column further.
- **Implementation subclasses must replace every inherited semantic default that changes.**
  See `ImplBase.effective_defaults()` for the merge contract. An OpenAI-compatible
  backend that omits its own `name` and `vendor` silently creates an OpenAI provider row.
