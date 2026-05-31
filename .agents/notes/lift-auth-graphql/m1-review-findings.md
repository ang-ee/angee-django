# M1 adversarial review — confirmed findings (wczldvpbm), 12 → 6 clusters

## 🔴 CRITICAL
1. **UserManager REBAC bypass** (`iam/models.py:31-37,95-106`; also #5,#7,#8). `UserManager.get()`
   sniffs kwargs via `_is_session_lookup` and routes any pk-keyed lookup through
   `system_context`, **bypassing REBAC framework-wide**. PROVEN: with no actor + STRICT_MODE,
   `User.objects.get(pk=X)` returns the user while `get(username=)`/`get(sqid=)` raise
   MissingActorError. Fires for ANY `User.objects.get(pk=...)` caller, not just session restore.
   Also NOT mirrored on async `aget()` → breaks under Daphne. **Fix (= p1's approach):** the
   AUTH BACKEND owns the no-actor session/credential fetch — a framework ModelBackend subclass
   whose `get_user`/`aget_user` wrap `system_context`; DELETE the `get()` override +
   `_is_session_lookup`. Alt (also p1-aligned): give `auth.User` a plain `BaseUserManager`
   (not RebacManager) so credential/session lookups need zero overrides. **→ the p1-shape
   refactor subsumes this.**
2. **Aggregate field-gate leak** (`notes/schema.py:159`; also #11,#12). `_scoped_note_queryset`
   uses `.on_field_deny("allow")` (disables field-read enforcement) AND `NoteGroupBy.IS_STARRED`
   groups by `is_starred`, which is field-gated (`read__is_starred = owner`). Grouping a gated
   column with enforcement off can leak the gated value via bucket counts. **Fix:** drop
   `on_field_deny("allow")` (rely on `_apply_scope_in_place` for row scope) and don't offer
   group-by on `gated_read_fields(Note)` (drop IS_STARRED) — or gate it behind its read perm.

## 🟠 HIGH
3. **AngeeConnection is redundant** (`base/graphql/node.py:24-51`; also #9). It re-applies
   `Meta.ordering` on the premise that `DjangoCursorConnection` falls back to pk — **that premise
   is false**; `DjangoCursorConnection` already honors `Meta.ordering`. (Contradicts the build
   agent's claim that it fixed a paging bug — verify, then) **Fix:** delete `AngeeConnection`,
   alias `Connection = DjangoCursorConnection`. Less code, identical behavior.
4. **crud delete id surface inconsistent** (`base/graphql/crud.py:127-154`). `deleteNote` takes a
   bare sqid via `instance_from_public_id`, while `node(id:)`/`updateNote` take a relay
   `GlobalID` — both render `ID!`, so the client can't use one id form. Delete path also untested.
   **Fix:** use `strawberry_django.mutations.delete` (relay GlobalID + REBAC-scoped node resolver).

## 🟡 MEDIUM
5. **`_merge_root` copy.copy field-mutation undo** (`base/graphql/schema.py:154-159`). Shallow-
   copies `StrawberryObjectDefinition.fields` to undo relay's in-place field mutation, because the
   SAME surface class (`NotesQuery`) is contributed to two schema names (public+console).
   **Fix the seam, not the symptom:** build each named schema's root from per-schema surface
   INSTANCES so no `StrawberryField` is shared (then no copy needed).

## ⚪ LOW
6. **`word_count` resolver shadows the model property** (`notes/schema.py:60-64`). Works only
   because `self` is the Django instance. **Fix:** declarative `word_count: int` (or
   `= strawberry_django.field(only=["body"])`) — no hand resolver.

**Cross-cut:** Clusters 1 (auth manager) is fixed BY the p1-shape refactor (rename iam→auth, p1's
plain manager + auth-backend session fetch). Clusters 2–6 are GraphQL/relay/aggregate cleanups
that persist regardless of iam→auth and should be folded into the same pass. Two invented
abstractions (AngeeConnection, copy.copy) confirm the "check the library/p1 before inventing" rule.
