# Decisions

A decision asks one question about one or more records. Its proposal offers fixed
alternatives: their labels, record actions and continuation outcomes. Human and
service users answer through the same entry point. A nullable verdict records the
chosen keys with `answered_by` and `answered_at`. Withdrawal records an empty
verdict with who stopped it; no verdict is the sole open-state rule.

`Decision.objects.ask(DecisionRequest(...), actor=actor)` admits one question.
Requests declare `kind`, saved `records`, `assignees`, `proposal`, and optional
`requester` and `context`. Omission makes the asking actor the requester;
explicit `None` allows self-assignment. Assignees must be active, and at least
one must be permitted to answer. Requesters cannot answer their own questions
without the administrative role. Admission requires standing read access to
concerns and context references for the asking actor, requester and assignees.
It creates no grants. Concern links use canonical model identities.

`DecisionProposal` is the Pydantic contract for the stored JSON:

```json
{"multiple": false, "alternatives": [
  {"key": "confirm", "label": "Use proposed name", "outcome": "confirmed",
   "actions": {"<record id>": {"fields": {"name": {"set": "Proposed name"}}}}},
  {"key": "keep", "label": "Keep what is on the record", "outcome": "confirmed"}
]}
```

At least one alternative and unique keys are required. Action identities must be
among the concerns. Field names must be editable scalar fields on their canonical
models; relation sets use readable public identities or null. Values and field
editability are validated when asking. Multiple alternatives cannot write the
same field of the same record. A record action may call only a method named by
the model's class-level `decision_methods` tuple, taking no required arguments;
optional keyword `arguments` are checked against its signature. `delete` is
never permitted. JSON null values are retained.

`Decision.objects.decide(decision, actor=actor, chosen=keys, revision=revision)`
validates distinct offered keys and the single/multiple choice rule, stores the
verdict in authored order, increments the optimistic revision and emits
`decision_answered` inside the answer transaction. Non-workflow askers apply there,
so refusal rolls the verdict back. Workflow subscribers schedule their wake after
commit and apply when the step resumes. The Python revision is optional; the card's
GraphQL mutation requires its observed revision. Invalid choices write nothing.
Decisions execute no proposal actions. The asker consumes the answer through the
records' owners. Free correction means editing the record and then choosing the
alternative that keeps its current values.

`DecisionContext` supplies typed facts and evidence references for the card.
Facts describe evidence; proposed fields live only in alternatives' actions.
The models are `Decision` and `DecisionRecord`.

`Decision.objects.open_for(record)` is a normal REBAC-scoped open queryset through
concern links. `records_with_open_decisions(queryset)` filters records using
`Exists` over all open questions concerning the readable records, pinned to the
records' queryset actor. Attention counts questions for anyone; the question's
own read scope still governs cards and answers.

Installed decisions contributes `has_open_decisions` to every Hasura model
resource through `ANGEE_GRAPHQL_RESOURCE_FILTERS`. Strawberry adds the annotation
only when selected, and the resource owner prepares its filter only when requested
(including nested Boolean predicates). Lists that use neither pay no Decision
permission compilation. There is no custom expression or separate scoping path.
`open_decisions(record_model, record_id)` returns readable open questions for a
readable record.

The [web fragment](web/README.md) exports `DecisionCard`, `fieldsToMark`,
`decisionFieldMarks`, `decisionAttentionColumn` and `openDecisionFilter`. Cards show each alternative and its per-record
changes, select radio buttons or checkboxes, and display the chosen verdict and
answer attribution after closure. The `decisions#origin` seam is supplied by
askers. `fieldsToMark` unions field names in all alternatives of open questions.

Schema-only migrations drop and recreate the old decision tables. Existing
decisions and workflow execution rows, including steps waiting on them, are
discarded. Incoming legacy links retire first; `StepRun.decision` belongs to the
workflow owner. There is no data conversion or compatibility layer. Fresh hosts
generate initial migrations from the current models.
