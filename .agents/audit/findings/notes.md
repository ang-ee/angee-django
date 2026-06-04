# Notes example consumer-addon + host audit

Scope: examples/notes-angee/src/example, examples/notes-angee/src/host
(runtime/ and web/ excluded). Judged against AGENTS.md, docs/guidelines.md,
docs/backend/guidelines.md, docs/stack.md. This is the exemplar consumers copy.

- id: notes-001
  loc: examples/notes-angee/src/example/notes/schema.py:144-146
  category: find-the-owner / delegate-to-library-owner
  severity: high
  rule: docs/backend/guidelines.md "Delegate to the library that owns the concern" + "REBAC is structural and owned by django-zed-rebac" + Django-Native Rule ("does Django/the library already own this fact"); AGENTS.md constitution "never re-derive ... from the outside what it already knows"
  finding: The exemplar reaches across the django-zed-rebac boundary into the private RebacQuerySet._apply_scope_in_place(), dodging the type checker with cast(Any, queryset), to eagerly scope by the ambient actor; the library's public scope accessors (as_user/with_actor/sudo) all take an explicit actor and there is no public ambient-eager-scope verb.
  fix: Add a public ambient-eager-scope accessor at the owning level (the rebac queryset, or an angee.base RebacQuerySet shim) and call that; the consumer must not call a library _private nor cast(Any, ...) to hide it.
  status: open

- id: notes-002
  loc: examples/notes-angee/src/example/notes/schema.py:213
  category: find-the-owner / cross-seam private reach
  severity: medium
  rule: docs/backend/guidelines.md "Put behavior on the object that owns the shape" + Framework Contracts "self-explaining ... public ... methods"; AGENTS.md "If the owner should answer but cannot, add the method there instead of writing a helper that reaches in"
  finding: _scoped_note_by_id calls Note._public_id_lookup(...), an underscore-private method of angee.base.AngeeModel, from the consumer addon (the only external caller); the public from_public_id uses the unscoped default manager and cannot be combined with the actor-scoped queryset, so the helper reaches into the framework private.
  fix: Expose a public scoped-by-public-id accessor on angee.base (e.g. a queryset.from_public_id / a public public_id_lookup the scoped queryset can use) and have the resolver call it.
  status: open

- id: notes-003
  loc: examples/notes-angee/src/example/notes/resources/demo/010_auth.user.yaml:1; examples/notes-angee/src/example/notes/apps.py:19
  category: naming / one-concept-one-name
  severity: medium
  rule: docs/guidelines.md "One concept, one name, everywhere"; docs/backend/guidelines.md Naming ("Django is the reference — match it exactly", packages "match the addon label"); AGENTS.md "a mismatch between code and docs is a bug"
  finding: The demo resource file is named 010_auth.user.yaml but declares model: iam.User; the sibling base addon sets the convention <order>_<applabel>.<model>.yaml (src/angee/iam/resources/master/010_iam.vendor.yaml), so post-IAM-lift this file (and the apps.py manifest entry) should read iam.user, not the stale auth label.
  fix: Rename to 010_iam.user.yaml and update the resources manifest entry in apps.py:19 to match the app label.
  status: open

- id: notes-004
  loc: examples/notes-angee/src/example/notes/schema.py:224-236
  category: DRY / "code is bigger instead of smarter"
  severity: low
  rule: docs/guidelines.md "Don't Repeat Yourself" + red flag "The code is bigger instead of smarter" ("copy-pasted variations that differ by only a value or two")
  finding: The public and console schema buckets repeat identical query/mutation/types lists verbatim, differing only by the console subscription line; unlike the IAM addon whose two surfaces genuinely diverge, here it is one near-duplicate block.
  fix: Bind the shared buckets once (e.g. a local notes_buckets dict) and spread it into both surfaces, adding only the console subscription.
  status: open
