# Checklists

A model declares a checklist, and results are recorded against each of its
items. A result has a status, a detail, the record's content token at recording,
and links to the records that produced it. The checklist is recorded, never
derived: readiness a model can compute stays a verb-eligibility condition
([Verb eligibility projections](../../../docs/backend/guidelines.md#verb-eligibility-projections)).

## Declare a checklist type

The owning addon depends on `angee-checklists` and composes `ChecklistMixin` on
the model's topmost REBAC-typed MTI ancestor. It declares the checklist and the
content token through classmethods, which composed donors extend through
`super()`:

```python
from angee.checklists.contracts import Checklist, ChecklistCategory, ChecklistItem
from angee.checklists.models import ChecklistMixin


class Inspection(ChecklistMixin, AngeeDataModel, concrete=True):
    @classmethod
    def checklist(cls) -> Checklist:
        return (
            super()
            .checklist()
            .extend(
                categories=(ChecklistCategory("identity", "Identity"),),
                items=(ChecklistItem("owner", "Owner known", category="identity", blocking=True),),
            )
        )

    @classmethod
    def checklist_revision_expression(cls) -> models.Expression:
        return TextSHA256(Cast(JSONObject(name=F("name"), notes=F("notes")), models.TextField()))
```

The token is an opaque string compared by equality. It covers the content the
checks read and excludes lifecycle and audit stamps. A child model may declare
the checklist of the rows its ancestor carries; the hooks are read on the
queried model.

The owning addon declares the type's relation in its `permissions.extends.zed`:

```zed
definition checklists/result {
    relation inspection: inspections/inspection // rebac:field=record
    permission target_read  = inspection->read
    permission target_write = inspection->write
}
```

`ChecklistResult.check` refuses the following:
- a misplaced mixin (`checklists.E001`);
- a type without a relation, or a relation without a type (`E002`, `E003`);
- duplicate keys, or unknown or reserved categories (`E004`);
- items without a content token (`E005`).

## Record results

```python
inspection.checklist_record("owner", ChecklistStatus.PASSED, "Owner confirmed.", evidence=(step,))
```

`ChecklistResultManager.record` refuses an undeclared key, an unknown status
and `not_checked`, which is the absence of a result rather than one. It stores
the record's current token and links each evidence record. Hold the record's
row lock from the check's reads until recording, so a concurrent edit cannot
be stamped as checked content. The result's `create` requires the type's
`target_write`. Evidence must be readable by the recording actor, and its type
declared by the recording addon:

```zed
definition checklists/evidence {
    relation step: jobs/step // rebac:field=record
    permission record_read = step->read
}
```

Deleting an evidence record deletes every result it supports, even one with
other links: the result's read depended on all of them, so it must not become
readable to more people. Its item reads `not_checked` again. Deleting the
checked record deletes its results. Both run through the owner's delete verbs
(`delete_supported_by` and `delete_for_record`) under a named system context,
so whoever may delete the record needs no authority on its results. They delete
through Django's collector: the links follow through their foreign key, and
every delete receiver runs (REBAC cleanup, record-edge policy, the change event
and the checked record's timeline refresh).

## Read

A result shows when its record is readable (the type's `target_read`) and
every evidence link is readable to the reader, signed in or anonymous
(`checklists/result#read`). The latest result per item wins. An unreadable
latest result shows nothing, never an older readable one.

`ChecklistStatuses` is the one status rule, in SQL:
- An item with no shown result is `not_checked`.
- A passed or not-applicable result recorded against another token, or against a NULL token, is `unresolved`; risks and conflicts stay.
- A category takes its strongest attention status. Otherwise it is `not_checked` when any item is, `not_applicable` when every item is, and `passed`.
- The record takes the strongest attention status, including results of keys that left the checklist while they need attention. It is `not_checked` when nothing is recorded, `passed` when every item has a fresh passing result, and otherwise `unresolved`.

`passed` means exactly that: every current item has a readable result recorded
against the current content. It does not wait for whichever process records the
results to finish. A consumer that needs "the process is done" gates on that
process (a run, a verb), not on the checklist.

The record's `target_read` and token are computed once per row; each item reads
its latest row and checks only its evidence (`checklists/result#evidence_hidden`).
That stands in for `read` only while the effective policy states
`read = target_read - evidence_hidden` with every `target_read` arm walking an
unfiltered `record`-backed relation (`read_decomposes`, read when the statement
compiles); an override that breaks it makes each result check `read` instead.

The proof and the result scopes are built as the statement compiles, inside the
policy snapshot REBAC pins for the fetch of a scoped queryset, so they read no
revision of their own: a count, a filter or a page of status cells is the rows'
one revision guard and one statement, however many records and checklist
expressions it holds. Compiled outside such a fetch, each scope reads its own.

These readers share the rule:
- The `checklist_status`, `checklist_<category>` and `checklist_next_blocking`
  filters, which reach every checklist type's resource through
  `ANGEE_GRAPHQL_RESOURCE_FILTERS`.
- `ChecklistResultManager.counts`, one grouped statement for dashboards. It
  returns each declared category with its label, in declared order.
- `ChecklistResultManager.assess`, for one record.
- `ChecklistNode`, a donor for the type's node. It reads a page with one
  annotation per row and one read of the page's latest results.

A consumer's gate reads `ChecklistAssessment.blocking_unmet`, for example a
decision's `validate_verdict`.

A recorded result publishes `checklistResultChanged` to readers holding its
record's `target_read`. The event names only the record, never the result, so a
result its evidence hides still refreshes the record's reads. A deletion has no
row left to evaluate `target_read` on, so it reaches the readers of the record
references it names: for a type whose `target_read` is narrower than its
record's read, a deletion event can reach a reader a creation event did not,
carrying nothing but the record reference.

Merging a checked record keeps its results on the merged record. The survivor's
statuses read only its own results, since its content token differs and a
carried-over pass would read stale anyway; the timeline still shows the merged
record's results through the survivor's redirect group.

## Timeline

Recorded results appear on the record's timeline as `checklists.result`. The
timeline is a chronology: it lists every result its reader may read, each row
authorized on its own. A result hidden by its evidence is absent there, while an
older readable result of the same item still appears, unlike the status rule,
which shows only the latest.

## Web

`@angee/checklists` registers the `checklist-<status>` tones and exports the
`ChecklistFields` fragment, the record section, the status, categories and
items columns and the legend. Each column reads the row's `checklist`
selection; the categories column shows the server's groups in order, with their
labels. A list refreshes when its results change by naming
`checklists.ChecklistResult` among its `relatedModels`.
