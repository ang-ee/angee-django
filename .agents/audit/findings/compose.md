# Compose addon structural audit

Scope: `src/angee/compose` (build-time composer). Judged only against
`AGENTS.md`, `docs/guidelines.md`, `docs/backend/guidelines.md`, `docs/stack.md`,
`docs/composer.md`. Read-only.

Adjudication summary: the composer already lives up to its constitution. The
plan/emit/check/reset lifecycle is one cohesive `AngeeRuntime` class (no loose
functions wrapped around a passive dataclass); find-the-owner is honored
(`get_composition_label`/`get_extension_target`/`get_extension_bases` live on the
model in `angee.base.models`, `model_classes`/`model_extensions` on `AppConfig`,
`is_composer_emitted` filtering happens in `base/apps.py`); ruff and mypy are
clean; the 6 compose tests pass; emitted output is sorted/deterministic. The
scanner's single candidate (the `apps.py:52` deferred import) is a legitimate,
rule-permitted phase-1 deferral, not a violation. Findings below are minor.

- id: compose-001
  loc: src/angee/compose/apps.py:48
  category: deferred-import
  severity: low
  rule: docs/backend/guidelines.md L104-117 "Mark such a deferral with a comment naming the reason" (phase-1 AppConfig deferral exception)
  finding: The phase-1 deferral of `from angee.compose.runtime import AngeeRuntime` is itself correct (hoisting pulls `angee.resources.models.Resource`, a model class, into the phase-1 import of the AppConfig module), but the comment names the wrong reason — it explains why introspection is safe in phase 2, not why importing AngeeRuntime must be deferred (its transitive model imports).
  fix: Reword the comment to state the actual deferral reason: importing AngeeRuntime at module top would transitively import model classes (`Resource`/`AngeeModel`) during phase-1 AppConfig load, before the registry is ready.
  status: open

- id: compose-002
  loc: src/angee/compose/runtime.py:389
  category: forwarding-wrapper
  severity: low
  rule: AGENTS.md "Keep one source of truth per fact"; docs/backend/guidelines.md L80-92 (find the owner)
  finding: `_ensure_cleanable` re-reads `settings.ANGEE_RUNTIME_DIR` to assert `self.runtime_dir` equals the configured dir, duplicating the settings read that `from_settings` (L78) already owns; an instance built via `from_addons`/`from_settings` already holds the authoritative `runtime_dir`, so the re-read is a second decode of the same fact rather than a property of the object.
  fix: Drop the re-read guard (or move the "is this the configured dir?" check to construction so the instance is trusted thereafter); keep one owner of the ANGEE_RUNTIME_DIR fact — `from_settings`.
  status: open

- id: compose-003
  loc: src/angee/compose/runtime.py:496
  category: type-switch-heuristic
  severity: low
  rule: docs/backend/guidelines.md L82-92 "Put behavior on the object that owns the shape ... ask `model._meta` rather than re-decoding model shape"; find-the-owner
  finding: `_history_excluded_fields` inspects each field's `.concrete`/`.is_relation`/`.auto_created` shape from outside to decide what simple-history cannot mirror — a per-field decision the composer decodes rather than asks an owner. Borderline: there is no Django/model owner for "fields simple-history excludes," the predicate is narrow and documented, and emission must run before `models_ready`, so this may be the smaller native shape rather than a real violation.
  fix: If the rule ever applies to more than one field kind, hang the predicate on the field (or a small named classifier) so the composer asks the field instead of branching on its attributes; otherwise leave as-is and treat this as monitored, not actionable.
  status: open
