# `workflows_ocr` to `workflows_extraction`

The extraction addon owns an explicit, data-preserving upgrade from the retired
`workflows_ocr` Django app label. Do not delete the old migration package, fake
its migrations, rename tables by hand, or run `makemigrations` ahead of
`angee build`.

## Retained history

`angee.workflows_ocr` is a migration-history-only addon. It contributes no
models, resources, schema, service, or web surface. Its AppConfig marker tells
the composer to retain the label in Django's migration graph. The composer
binds `MIGRATION_MODULES["workflows_ocr"]` to the generated
`runtime/workflows_ocr/migrations` package. That package searches the
deployment's preserved migration files first and the source addon's empty
`0001_initial` anchor second. Normal Django migration generation therefore
writes only to the deployment-owned runtime tree.

Keep every generated `workflows_ocr` migration file already shipped with a
deployment. The empty source anchor is for a new installation; it is not a
replacement for released history. Historical extraction migrations also keep
the legacy `ANGEE_OCR_ENGINE_CLASSES` name bound to the canonical extraction
registry so their serialized `ImplClassField` can reconstruct during app
loading without restoring a second active configuration surface.

Before normal provision, rename deployment overrides from the retired prefix to
their active extraction names: `ANGEE_OCR_ENGINE_CLASSES` →
`ANGEE_EXTRACTION_ENGINE_CLASSES`, `ANGEE_OCR_MAX_BYTES` →
`ANGEE_EXTRACTION_MAX_BYTES`, `ANGEE_OCR_MAX_PAGES` →
`ANGEE_EXTRACTION_MAX_PAGES`, `ANGEE_OCR_MAX_EDGE` →
`ANGEE_EXTRACTION_MAX_EDGE`, `ANGEE_OCR_DPI` → `ANGEE_EXTRACTION_DPI`,
`ANGEE_OCR_TIMEOUT_SECONDS` → `ANGEE_EXTRACTION_TIMEOUT_SECONDS`, and
`ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS` →
`ANGEE_EXTRACTION_APPROVED_MODEL_DEPLOYMENTS`. The historical registry alias is
only for migration reconstruction; active serving code does not read old
settings. Provider-specific optical names such as the GLM engine key and model
deployment identifiers remain unchanged.

## Supported states

The runtime migration guards recognize these complete states:

- Neither old nor current model state: a fresh installation uses ordinary
  generated current-app migrations; the retired anchor creates no tables.
- Complete old state and no current state: the staging migration creates the
  complete current extraction schema, then adoption copies the old rows.
- Complete old and current state with empty current tables: adoption copies the
  retained rows and identities.
- Complete current state and no old state: the transition is already complete.

Any partial old/current model graph, partial old table set, missing destination
column, occupied destination table, primary-key collision, incompatible auth or
content-type fact, or unsupported persisted-reference format fails the build or
migration transaction. Before upgrading, verify every retained extraction has a
terminal `succeeded` or `failed` status and an engine key registered in
`ANGEE_EXTRACTION_ENGINE_CLASSES`. The migration fails closed on any transient
status or retired engine such as `inference_document`; retained evidence remains
immutable, so the missing implementation must be restored before retrying.
Adoption preserves primary keys, audit timestamps, JSON values, and foreign-key
identities, then resets destination sequences. Legacy
rows did not retain logical document identities. Adoption allocates stable
UUID5 identities from each preserved row and selector without asserting
correspondence across old revisions. A row with an explicitly stored
`engine_config.evidence_layout` keeps its declared document and line selectors;
any other nonempty result becomes one selectable root document with no inferred
lines, and an empty failed result remains selectorless. A later current-profile
revision may retain that root and allocate reviewed line identities. Adoption
also creates exact lineage heads, retargets the four extraction REBAC
namespaces, and rewrites only declared model-reference columns and scoped
django-reversion JSON. It does not recursively rewrite arbitrary audit payloads.

Old tables remain until every historical model field and every database foreign
key outside the retired table set has moved to the current models. Cleanup
checks both Django `ProjectState` and database constraints before dropping
anything. `workflows_ocr` stays protected from autodetected `DeleteModel`
operations while that cleanup is unavailable.

## Staged cutover

Run the stack's normal provision command. Its first `angee build` may safely
materialize current-schema and adoption nodes, then stop at the protected
history guard when a downstream migration still points at `workflows_ocr`.
This stop occurs before `makemigrations` and before any database migration. Keep
the staged files. A source-field edit alone cannot move a historical Django
field: the blocked build intentionally prevents the following unrestricted
`makemigrations` command. The addon that owns each persisted downstream foreign
key must declare a self-contained runtime migration with a native `AlterField`
to the `workflows_extraction` model and a dependency on
`("workflows_extraction", "__latest__")`. The next `angee build` materializes
those consumer moves before reevaluating retirement. Follow the general
[runtime-migration rules](guidelines.md#migrations-and-runtime); do not bypass
the guard with a direct or unrestricted `makemigrations` invocation.

When all references can move, the same build materializes guarded retirement.
Native provisioning then continues in this order:

1. `makemigrations --skip-checks`
2. `migrate --noinput --skip-checks`
3. `reconcile_permissions`
4. `rebac --skip-checks sync --yes`
5. `check`
6. resource loading and schema emission

Adoption retargets persisted grant identities before the empty retired
permission package is reconciled away; current extraction definitions are then
synced by their owning package. A guard failure leaves the old database rows in
place and prevents later provision steps. Resolve the named state or collision
and retry from normal provision.
