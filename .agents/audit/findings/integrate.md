- id: integrate-001
  loc: src/angee/integrate/models.py:217
  category: lifted/speculative code (type-switch wanting model-owned declaration)
  severity: medium
  rule: docs/guidelines.md "Avoid Red Flags" (unearned generality) + docs/backend/guidelines.md Naming/owner ("Declaration facts live on the declaring object"); events.py owns EventKind yet deliver_event types kind as Any and re-coerces it
  finding: deliver_event has no production caller (only tests) and types kind as Any then does getattr(kind,"value",kind), bypassing the EventKind vocabulary the addon ships in events.py.
  fix: type the seam against its owner — `kind: EventKind` (drop the Any + getattr coercion); if no producer is intended yet, delete EventKind until one exists.
  status: fixed

- id: integrate-002
  loc: src/angee/integrate/models.py:120
  category: type-switch / dead defensiveness on an untyped contract
  severity: medium
  rule: docs/backend/guidelines.md "Put behavior on the object that owns the shape" (coerce at the owner, do not branch on type from outside); docs/guidelines.md red flag "dead defensiveness"
  finding: record_sync guards `result if isinstance(result, int) and not isinstance(result, bool) else 0` because Bridge.sync() returns Any; the base sniffs the subclass's return type instead of the contract declaring it.
  fix: make Bridge.sync() return a typed int item-count (or have record_sync take `items: int`) so the isinstance guard disappears.
  status: fixed

- id: integrate-003
  loc: src/angee/integrate/__init__.py:3
  category: docs<->code drift (prose referencing an ephemeral plan id)
  severity: low
  rule: AGENTS.md "a mismatch between code and docs is a bug"; docs/guidelines.md "Let Code Carry Code Contracts" (do not keep a second spec in prose); D6/D7 are defined only in .agents/plans/integration-lift.md, not a durable doc
  finding: the shipped package docstring says concrete capabilities live in domain addons "per D6/D7" — a dangling reference to private plan identifiers that vanish when the plan is archived.
  fix: state the contract directly ("concrete capabilities live in domain addons") without citing plan-local D6/D7 labels.
  status: fixed

- id: integrate-004
  loc: src/angee/integrate/models.py:77
  category: type-switch / DRY (choice-value coercion duplicated a fourth way)
  severity: low
  rule: docs/backend/guidelines.md ("coerce values ... instead of branching on field type from outside"); AGENTS.md DRY ("same shape in three places: extract the smallest boring primitive") — getattr(x,"value",x) coercion already exists in iam/models.py:778 and base/apps.py:316
  finding: report_status branches `status.value if isinstance(status, CapabilityStatus) else status`, a fourth inline re-implementation of "get a TextChoices value" that the codebase already does three other ways.
  fix: use the established idiom `str(getattr(status, "value", status))` (CapabilityStatus is a str-enum), or promote the repeated coercion to one shared owner the three sites reuse.
  status: fixed
