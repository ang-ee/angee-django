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
    relation inspection: field/inspection // rebac:field=record
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

`ChecklistResultManager.record` refuses an undeclared key or status. It stores
the current token and links each evidence record. The result's `create`
requires the type's `target_write`. Evidence must be readable by the
recording actor, and its type declared by the recording addon:

```zed
definition checklists/evidence {
    relation step: jobs/step // rebac:field=record
    permission record_read = step->read
}
```

## Read

A result shows when its record is readable and every evidence link is
readable (`checklists/result#read`). The latest result per item wins. An
unreadable latest result shows nothing, never an older readable one.

`ChecklistStatuses` is the one status rule, in SQL:
- An item with no shown result is `not_checked`.
- A passed or not-applicable result recorded against another token is `unresolved`; risks and conflicts stay.
- A category takes its strongest attention status. Otherwise it is `not_checked` when any item is, `not_applicable` when every item is, and `passed`.
- The record takes the strongest attention status, including results of keys that left the checklist while they need attention. It is `not_checked` when nothing is recorded, `passed` when every item has a fresh passing result, and otherwise `unresolved`.

These readers share the rule:
- The `checklist_status`, `checklist_<category>` and `checklist_next_blocking`
  filters, which reach every checklist type's resource through
  `ANGEE_GRAPHQL_RESOURCE_FILTERS`.
- `ChecklistResultManager.counts`, one grouped statement for dashboards.
- `ChecklistResultManager.assess`, for one record.
- `ChecklistNode`, a donor for the type's node. It reads a page with one
  annotation per row and one read of the page's latest results.

A consumer's gate reads `ChecklistAssessment.blocking_unmet`, for example a
decision's `validate_verdict`.

Recorded results appear on the record's timeline as `checklists.result`.
