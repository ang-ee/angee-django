# Decisions

A decision asks one question about one or more records. Its proposal offers fixed
alternatives: their labels, record actions and continuation outcomes. Human and
service users answer through the same entry point. A nullable verdict records the
chosen keys with `answered_by` and `answered_at`; only an answer closes a question.

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
models; relation sets use public identities. A record action names a public
method. JSON null values are retained. Creation revalidates the entire proposal.

`Decision.objects.decide(decision, actor=actor, chosen=keys, revision=revision)`
validates distinct offered keys and the single/multiple choice rule, stores the
verdict in authored order, increments the optimistic revision and emits
`decision_answered` after commit. The Python revision is optional; the card's
GraphQL mutation requires its observed revision. Invalid choices write nothing.
Decisions execute no proposal actions. The asker consumes the answer through the
records' owners. Free correction means editing the record and then choosing the
alternative that keeps its current values.

`DecisionContext` supplies typed facts and evidence references for the card.
Facts describe evidence; proposed fields live only in alternatives' actions.
The models are `Decision` and `DecisionRecord`.

`Decision.objects.open_for(record)` is a normal REBAC-scoped open queryset through
concern links. `records_with_open_decisions(queryset)` filters records using
`Exists` over that same readable-decision queryset, pinned to the records'
queryset actor. Hidden questions provide no visible attention.

Installed decisions contributes `has_open_decisions` to every Hasura model
resource through `ANGEE_GRAPHQL_RESOURCE_FILTERS`. Strawberry adds the annotation
only when selected, and the resource owner prepares its filter only when requested
(including nested Boolean predicates). Lists that use neither pay no Decision
permission compilation. There is no custom expression or separate scoping path.
`open_decisions(record_model, record_id)` returns readable open questions for a
readable record.

The [web fragment](web/README.md) exports `DecisionCard`, `RecordDecisions`,
`DecisionsList`, and `fieldsToMark`. Cards show each alternative and its per-record
changes, select radio buttons or checkboxes, and display the chosen verdict and
answer attribution after closure. The `decisions#origin` seam is supplied by
askers. `fieldsToMark` unions field names in all alternatives of open questions.

Schema-only migrations retire the old subject/expiry fields and then groups,
forms, policies, rounds and answer payloads. The workflow owner retires group
waits and contributes the direct decision-to-step link; intake removes its copied
origin link. They do not convert stored questions, verdicts or runs. Existing data
and external callers require their own cutover. Fresh hosts generate initial
migrations from the current models.
