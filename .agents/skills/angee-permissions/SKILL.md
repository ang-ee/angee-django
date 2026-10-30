---
name: angee-permissions
description: "Declares and wires REBAC permissions for an Angee addon: permissions.zed, roles and role anchors, permissions.extends.zed fragments, rebac_resource_type, rebac_grantable, field gates, field-backed conditional access, resolver delegation, system context, rebac sync, per-actor tests."
---

# Angee Permissions

## Use when

- "Add a `permissions.zed` for the new model" / "who can read or write `<app>/<model>`".
- "Let `<consumer>` reviewers see another addon's records" / "extend `messaging/thread` read".
- "Make the record shareable" / "add a `reader` grant" / "gate the `salary` field".
- "Access depends on a column" / "only when `visibility = record`" / "public rows".
- "`rebac.E009`", "permission denied after a zed change", "write a test that proves alice sees it and bob does not".

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/permissions.zed` | Every definition the addon owns: relations, permissions, revision header | `examples/src/example/notes/permissions.zed:10` (`notes/note`), `:32-33` (ownership gate) |
| `<addon>/permissions.extends.zed` | Relations, uniquely owned permission declarations and arms contributed to definitions owned elsewhere | `examples/src/example/notes/permissions.extends.zed`, `tests/extcontrib/permissions.extends.zed` |
| `Model.Meta.rebac_resource_type` | Binds a model to its definition | `examples/src/example/notes/models.py:22` (class), `:67` (option) |
| `angee.base.models.roles.role_anchor` | Table-less anchor model for a `<ns>/role` namespace | `src/angee/base/models/roles.py:14`, `src/angee/tags/models.py:341` |
| `Model.rebac_grantable` + `AngeeModel.grant_record_access` | The direct-share surface and its permission policy | `src/angee/base/models/base.py:88`, `:160`, `:312` (`require_access`) |
| `src/angee/base/build.py` | Fragment merge, field-gate ownership, emitted `runtime/permissions/<package>.zed`, effective schema artifact projection; AppConfig owns the schema declaration | `merged_schemas` `:202`, `_merge_definition` `:267`, `schema_path_wiring` `:394`, `composed_grants` `:494`, `field_gate_owners` `:509` |
| `src/angee/base/permissions.py` | `declared_grants` `:23`, `effective_rebac_definition` `:50`, `subject_reaches_user` `:120`, `candidate_relationships` `:180` | whole file |
| `src/angee/base/checks.py` | `angee.E024` (no caveats) `:37`, `angee.E045` (`owner` relation needs `OwnerMixin`) `:71` | whole file |
| `src/angee/base/models/ownership.py` | `angee.E022` `:134` and `angee.E027` `:171`: the `transfer` + `write__owner` gate every `OwnerMixin` model's definition carries | `OwnerMixin.check` `:91` |
| `src/angee/iam/permissions.zed` | `auth/user` `:6`, `iam/kind` `:104`, `auth/group` `:110`, `angee/role` `:120` — the subjects every addon targets | whole file |
| `src/angee/graphql/actions.py` | `authorized_action_target` `:240`, `authorized_permission_target` `:276` resolver preflight | `src/angee/graphql/trash.py:57` (`trash_target`) |
| `src/angee/base/apps.py` | REBAC synchronization contribution to upstream provisioning | `BaseConfig.compose_provision`; explicit `ANGEE_REBAC_FORCE_OVERWRITE` setting |

Parsing, sync, point checks, actor-scoped querysets and gated-field projection are
`django-zed-rebac` (`docs/stack.md#backend`, line 76); `composed_tables`
(`src/angee/base/testing/fixtures.py:58`) and `composed_permissions`
(`src/angee/base/testing/permissions.py:41`) sync it into the test database.

## Reuse first

- Subjects: `auth/user`, `auth/group#member`, `angee/role:admin#member`, `authenticated` (iam zed above). Never declare a second user, group or admin vocabulary.
- Universal admin reach: `relation admin: angee/role // rebac:const=admin` + `admin->member` (every definition in `src/angee/tags/permissions.zed`, `src/angee/money/permissions.zed`).
- Named addon role: `definition <ns>/role` + `role_anchor("<ns>/role")` + `relation manager: <ns>/role // rebac:const=<key>` + `manager->effective_member` (tags, money, storage, portfolio).
- Owner column: compose `angee.base.models.ownership.OwnerMixin`, back `owner` with `rebac:field=owner`, declare `transfer` and `write__owner = transfer` (notes). `angee.E045` rejects any other `owner` relation; `angee.E022/E027` reject a missing gate.
- Row-dependent sharing: `rebac:field={"path":…,"filters":{…}}` (notes extends `storage/file`), `rebac:const={"target_id":"shared","filters":{…}}` + `shared_reader = authenticated` (money/rate, dashboards).
- Live user facts: `rebac:attribute={"field":…,"value":…}` on `iam/kind`; compose `iam/kind:person#active_member`, never a copy.
- Edge seams other addons already own: `tags/tag_assignment`, `storage/file_attachment`, `knowledge/record_binding`, `messaging/thread_attachment`, `projects/task`, `decisions/decision` — each declares `target_*`/`subject_*` arms for you to union into (notes extends file).
- Native API: `.with_actor(user)`, `.as_user(user)`, `.with_action(perm)`, `.for_write()`, `.readable(field)`, `.sudo(reason=…)`, `.system_context(reason=…)` (rebac `managers.py:719-858`), `has_access` (rebac `mixins.py:1079`), `check_new` (rebac `preflight.py:147`); Angee adds `AngeeModel.require_access` (`base.py:312`) and `rebac_grantable` + `angee.graphql.sharing` for shares.

## Steps

1. **Bind the model to a resource type** in the abstract source model's `Meta` (notes; mixin list trimmed).
   ```python
   class Note(OwnerMixin, AuditMixin, AngeeDataModel):
       rebac_grantable = {"reader": "share", "editor": "share"}   # step 5

       class Meta:
           abstract = True
           rebac_resource_type = "notes/note"
   ```
   If the addon needs a named role → declare `definition notes/role { relation member: auth/user | angee/role:admin#member }` in the zed and, beside the models, `NotesRole = role_anchor("notes/role")` (module-level, direct call; the bound name must match `name`, `roles.py:14-45`).
   **Owner rule:** `rebac_resource_type` is a library `Meta` option (`REBAC_META_OPTIONS`, `build.py:468`) that the composer carries onto the runtime class; a registry, AppConfig attribute or string table naming the same binding is the duplicate `docs/guidelines.md#dont-repeat-yourself-dry` tells you to delete.

2. **Write `permissions.zed`** beside `models.py` (one per app; `AppConfig.rebac_schema` declares the native schema path). This is the complete `notes/note` definition; a model composing `OwnerMixin` needs the `transfer` + `write__owner` pair or `angee.E022`/`angee.E027` fire (`ownership.py:134`, `:171`).
   ```zed
   // @rebac_package: notes
   // @rebac_package_version: 0.1.0
   // @rebac_schema_revision: 6

   definition notes/note {
       relation owner: auth/user // rebac:field=owner
       relation editor: auth/user | auth/group#member
       relation reader: auth/user | auth/group#member
       relation reviewer: auth/user // rebac:field=reviewer
       relation admin: angee/role // rebac:const=admin
       relation trigger_editor: notes/role // rebac:const=trigger_editor

       permission create = authenticated
       permission read   = owner + editor + reader + reviewer + trigger_editor->member + admin->member
       permission write  = owner + editor + trigger_editor->member + admin->member
       permission share  = owner + editor + admin->member
       permission delete = owner + admin->member
       permission transfer = owner + admin->member
       permission write__owner = transfer
       permission read__is_starred  = owner
       permission read__reminder_at = owner
       permission write__status = owner + editor + trigger_editor->member + admin->member
   }
   ```
   Bump `@rebac_schema_revision` on every edit of this file. A relation stores a plain subject or userset; arrows compute reach (`manager->effective_member`), never `#effective_member` in an allowed subject. Parenthesize `(a - b) + c`. Write `read` so the store can scope lists; `create` is evaluated on the unsaved candidate's field/const relations.
   **Owner rule:** reach is schema, membership is data (`rebac.memberships`, grants). When a subject must reach a row, the arm belongs here: `docs/backend/guidelines.md#rebac` ("Visibility and access are REBAC-native, always", line 650) rules out a Python provider, `visible_to` or queryset filter as the access decision; Python still narrows a verb's candidate rows.

3. **Express row-dependent access as a live backed relation, never a caveat.** Three shapes, all evaluated against the Django rows:
   ```zed
   // a related row admits only under a column condition (notes → storage/file)
   relation note_record: notes/note // rebac:field={"path":"attachments__note","filters":{"visibility":"record"}}
   permission read = note_record->read

   // the row's own columns admit everyone signed in (money/rate, dashboards)
   relation shared: money/rate // rebac:const={"target_id":"shared","filters":{"context_object_id":""}}
   permission shared_reader = authenticated          // arrow-only; check `read`, never this
   permission read = shared->shared_reader + admin->member

   // a live column fact as a fixed container (iam/kind)
   relation active_member: auth/user // rebac:attribute={"field":"kind","resource":"person","value":"person","filters":{"is_active":true}}
   ```
   If the condition is a request value (time, IP, a flag the caller passes) → it is not expressible; scoped querysets carry no caveat context (`angee.E024`, `checks.py:37`). Put the fact on a column and back the relation with it.
   **Owner rule:** the column owns the fact and the zed reads it (`docs/backend/guidelines.md#rebac`, "no tuple mirrors the column", line 702; `docs/guidelines.md#put-behavior-on-the-owning-object`). A tuple mirror or a Python re-decision of the same condition is a second implementation; orchestrating the verb around the check is not.

4. **Contribute to another addon's definition from `permissions.extends.zed`** (sibling file, own revision header). Name only existing definitions; add relations and uniquely owned capability permissions, and union arms into permissions the base declares. New field gates additionally require that same addon's concrete donor column.
   ```zed
   // @rebac_package: notes
   // @rebac_schema_revision: 3

   definition storage/file_attachment {
       relation note: notes/note // rebac:field=target
       permission target_write = note->write
   }

   definition messaging/thread {
       relation note_record: notes/note // rebac:field=…
       permission read_granted = note_record->read   // the owner derives read from read_granted
   }
   ```
   If your role must be elevated → union it into `iam/protected#member`; if it manages people → union into `auth/user#list`/account verbs (`tests/extcontrib/permissions.extends.zed`). If the model composes `TaggedModel` → declare the `target`-backed relation on `tags/tag_assignment` or `tags.E002` (`src/angee/tags/models.py:296`) fails startup. Collisions (reused relation or newly introduced permission names), unknown targets, and native-invalid references raise `SchemaExtensionError` (`build.py:98`).
   **Owner rule:** the target addon owns the seam, the contributor owns its vocabulary and revision (`docs/backend/guidelines.md#rebac`, "A consumer addon contributes…", line 765, and the revision rule at line 788). The composer emits the merged `runtime/permissions/<package>.zed` (`docs/composer.md#flow`); do not hand-edit or commit it.

5. **Declare direct sharing and field gates on the model.** `rebac_grantable` maps a stored (unbacked) relation to the permission that manages it; `angee.E015-E017` (`base.py:373-406`) validate it against the effective definition. Field gates are `read__<field>` / `write__<field>` permissions in the zed (`write__<field>` is enforced on update only; keep protected creation values out of insert inputs).
   - Gates in the owning zed: any permission. Gates in a fragment: only on a concrete column *your* package's donor (`extends` model) contributes to that target; `_id` attnames, base and many-to-many fields are refused and a second package cannot extend your gate (`_merge_definition`, `build.py:267-318`).
   - A materialized MTI child inherits no `rebac_grantable` from its concrete parent (`build.py:474-476`, `composed_grants` `:494`); declare the child's own surface or it has none.
   - Gated fields project as `None` under the default `REBAC_FIELD_READ_MODE="redact"` (`src/angee/base/autoconfig.py:31`); they are never filterable or sortable. Filter on `queryset.readable(field)` (`src/angee/iam/models.py:228`); write targets load with `.for_write()`.
   **Owner rule:** `rebac_grantable` is the one share declaration; `grant_record_access`/`revoke_record_access`/`direct_record_access` and `validate_record_access_target/subject` (`base.py:160-301`) own the policy and GraphQL sharing dispatches to them (`docs/backend/guidelines.md#direct-record-access`).

6. **Delegate from resolvers and verbs.** Resolve the target through the exact permission and let the owner act:
   ```python
   target = authorized_permission_target(info, model, target_id, "delete",
                                         narrow=lambda rows: rows.trash_targets())
   target.trash(reason=reason)                     # src/angee/graphql/trash.py:57-74
   ```
   - Instance verbs: `self.require_access("write")`; unsaved rows: native `create` on `insert()`, or `candidate_relationships(instance, action).allows(subject, action)` (`permissions.py:180`) for a preflight on a row that is not the stored one.
   - Framework-internal writes: `with system_context(reason="<owner>.<verb>")` only for named system work (`audit.py:28` delete retention, `ownership.py:305` transfer validation). `system_grant_record_access` requires that scope (`base.py:178`).
   - Two-phase destructive verbs check the public permission, then run the destructive work elevated, and test the deny branch.
   **Owner rule:** the resolver's budget is input, context, dispatch, result; the access decision lives in the zed and the model (`docs/guidelines.md#keep-policy-above-detail`, `docs/backend/guidelines.md#graphql-actor-and-write-contracts`). Cross-owner orchestration inside the verb is legitimate under `docs/guidelines.md#put-behavior-on-the-owning-object`; a second decision is not.

7. **Sync the stored schema.** `rebac sync` parses every effective `permissions.zed` (the merged file for extended packages) and persists it into the `Schema*` tables; the system checks compare models with *that stored* schema, so they gate every `rebac` subcommand (`docs/backend/guidelines.md#rebac`, line 813). Three distinct operations:
   - Ordinary sync after a zed edit on a running stack: `rebac sync` (`docs/checks.md#composition-and-schema`), then restart Django through the operator (`docs/howto/getstarted.md#restart-the-running-stack`). `rebac sync --check` is the drift gate; `rebac check` validates without writing.
   - Retirement of removed definitions, packages or backings: provision's sync step runs `--skip-checks sync --prune-stale --yes` after `migrate` and before `check` (`provision.py:58`, `docs/backend/guidelines.md#rebac`, line 825). Run the same shape, in that order, when a type was renamed or removed.
   - Deliberate overwrite: `--force-overwrite` (explicit `ANGEE_REBAC_FORCE_OVERWRITE` setting) replaces locally managed live policy. Use it only when that replacement is the decision (`docs/backend/guidelines.md#rebac`, line 829); it is not part of an ordinary sync.
   If `rebac.E009` names a backing you just renamed, removed or added → the stored schema, not the source, is stale, and the check blocks the very sync that would fix it: run the retirement shape above with `--skip-checks` (`migrate --skip-checks` first when migrations are pending), then `check`. `rebac.E009` is the library's backing check (`rebac/checks.py:321-331`); a role or anchor type it cannot resolve needs its `role_anchor` (`roles.py:25`).

   **Owner rule:** sync, prune and overwrite are the library's command options; Angee adds only the merged-file emission and path wiring (`build.py:20-24`) and provision's step order (`provision.py:49`).

8. **Write permission tests that assert visibility per actor.** Use a non-admin reader and a non-reader; assert through the actor-scoped manager and the point check, not through `sudo()`.
   ```python
   def test_reader_grant_redacts_owner_only_fields(self) -> None:
       self.welcome.with_actor(self.alice).grant_record_access("reader", self.bob)
       bob_view = Note.objects.as_user(self.bob).get(sqid=self.welcome.sqid)
       self.assertIsNone(bob_view.is_starred)            # read__is_starred = owner
       self.assertEqual(list(Note.objects.as_user(self.carol).filter(pk=self.welcome.pk)), [])
   ```
   Composed host (`examples/src/example/notes/tests/test_rebac.py:23-28`): `TransactionTestCase`, `call_command("rebac", "sync")` in `setUp`, seed through `resources load`. Source-addon tests (`tests/test_scopedemo.py:34-39`): request `composed_tables` or `composed_permissions` (merges fragments, syncs with `--force-overwrite` into the disposable test database) and `backend().check_access(subject=…, action=…, resource=to_object_ref(row))`. Field-backed and const-backed reach leaves no tuples: assert `active_relationship_model().objects.filter(resource_type=…)` is empty when that is the design.
   **Owner rule:** tests exercise the composed runtime model and the schema the fixtures sync (`docs/checks.md#source-addon-test-models`); a test-local schema is justified only where no addon owns the definition, as `tests/scopedemo/permissions.zed` does.

## Do not

- Caveated subjects or `with` contexts in any zed → `angee.E024` (`src/angee/base/checks.py:37`); use step 3.
- A Python provider, `visible_to`, `is_superuser` branch or queryset filter as authorization → `docs/backend/guidelines.md#rebac` ("Visibility and access are REBAC-native, always", line 650).
- Editing a framework addon's `permissions.zed` to name your role → `docs/backend/guidelines.md#rebac` (line 765); use `permissions.extends.zed`.
- A fragment that declares a new non-gate permission → `_merge_definition` (`build.py:267`) raises. A `read` arm on a definition that owns `read_granted` (`messaging/thread`) bypasses its withholding → `docs/backend/guidelines.md#rebac` ("Permission fragments widen `read_granted`", line 503); union there.
- `#effective_member` inside a relation's allowed subjects → invalid schema; store the role and arrow (`docs/backend/guidelines.md#rebac`, "Relations constrain stored subjects", line 601).
- Mirroring a column into tuples, or a remote backend → `docs/stack.md#backend` (local backend only).
- A private share mutation, Django group M2M, or `auth.Permission` sync → `docs/backend/guidelines.md#rebac` ("Declare direct sharing once", line 553; "Membership has one store", line 609).
- `system_context` around a user-requested write because it inserts a row → `docs/backend/guidelines.md#rebac` (line 712).
- Checking `shared_reader` or any arrow-only permission directly or as a field gate → `docs/backend/guidelines.md#rebac` ("Row-dependent shared visibility", line 690).
- `--force-overwrite` as the routine zed sync → `docs/backend/guidelines.md#rebac` (line 829); step 7 names the three operations.
- Hand-editing `runtime/permissions/*.zed` or committing it → `docs/composer.md#flow` (generated output).

## Verify

Validator: the addon's focused test module plus the system checks; the loop is `.agents/skills/slice/SKILL.md`.

- In a jj lane (no host, database or JS install): the focused Python tests, `tests/test_permission_composition.py`, `tests/test_layering.py`, lint and types on touched files, all under `docs/checks.md#python-and-composition-contracts`. Per-actor assertions run here because `composed_tables`/`composed_permissions` sync REBAC into the SQLite test database (`docs/checks.md#source-addon-test-models`).
- Only on integrated `src`: composition drift, `check` (`angee.E015-E017`, `E022`, `E024`, `E027`, `E045`, `rebac.E009`, `tags.E002`), the stack sync and the composed example suite under `docs/checks.md#composition-and-schema`; the live page under `docs/checks.md#browser-verification`; CI scope under `docs/checks.md#what-ci-actually-runs`.
- Done means: the test module proves read/write/deny for at least owner, grantee and outsider; `check` is clean; a grant through the share surface appears in `direct_record_access()`; a backed relation leaves no stored tuples.
- False greens: a green unit suite with a live "loaded N rows outside actor scope" failure (`docs/backend/guidelines.md#rebac`, line 797); admin-only tests that hide a leaked system scope (line 712).

## Pitfalls

- The stored schema gates every `rebac` subcommand and `check`; after a rename or retirement `rebac.E009` describes the store, not the source (`docs/backend/guidelines.md#rebac`, line 813; step 7).
- `role_anchor` wrapped in a helper captures the helper's module and the composer fails at emission on the mis-capture; call it at module level or pass `module=__name__` (`src/angee/base/models/roles.py:39-45`).
- A const-backed role target without its own `definition` + anchor makes a non-member check raise instead of deny (`docs/backend/guidelines.md#rebac`, "Const-backing", line 751).
- SQLite's `REBAC_DEPTH_LIMIT` of 8 silently drops rows inherited through deeper hierarchies (`docs/backend/guidelines.md#rebac`, line 820; `src/angee/base/autoconfig.py:55`).
- `select_related` of a REBAC-guarded relation inside an actor-scoped queryset passes unit tests and fails live (`docs/backend/guidelines.md#rebac`, line 796).
- Force-login in tests needs the IAM backend ([authorization pitfalls](references/pitfalls.md)).

## Read next

- [Authorization pitfalls](references/pitfalls.md)
- `docs/backend/guidelines.md#rebac`, `#graphql-actor-and-write-contracts`, `#direct-record-access`, `#rules`
- `docs/stack.md#backend` (django-zed-rebac row), `docs/glossary.md` (REBAC, Actor, Role, Relation, Binding, Kind)
- `docs/checks.md#composition-and-schema`, `docs/composer.md#flow`, `docs/howto/getstarted.md#restart-the-running-stack`
- Skills: `slice`, `angee-workspace`
