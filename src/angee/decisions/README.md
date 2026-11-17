# Decisions

A decision asks one question about one or more records. Its proposal offers fixed
alternatives: their labels, record actions and continuation outcomes. Human and
service users answer through the same entry point. A nullable verdict records the
chosen keys with `answered_by` and `answered_at`. Withdrawal records an empty
verdict with who stopped it; no verdict is the sole open-state rule.

`Decision.objects.ask(DecisionRequest(...), actor=actor)` admits one question.
Requests declare `kind`, saved `records`, `proposal`, and optional `assignees`,
`requester`, `context`, `due_at` and `remind_after`. Assignees default to `()` and requester to `None`.
When supplied, assignees must be active and at least one must be permitted to
answer. The composed `act` permission governs answers, including an owning
addon's `owner_eligible` contribution. Without such a contribution or named
assignees, only active administrators may answer. Admission requires at least
one participant and standing read access to concerns and context references for
the asking actor, requester and assignees. It creates no grants. Concern links
use canonical model identities. An owning addon may declare
`RetainedGenericRelation("decisions.DecisionRecord", related_query_name=...)` on the
canonical concerned model and contribute typed `owner_eligible` and `read_granted` arms
through that query path. Deleting the source preserves these retained links.
`decisions/decision#read_granted` is the assignees, the requester and administrators,
and `read` derives from it. A consumer whose outside audience reads the question asked
of them, such as a represented party's admitted user, unions that arm into
`read_granted` from its own `permissions.extends.zed`; it reaches the question and its
concern links and nothing else; the answer's referenced records still show only to
their own readers.

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
models or updatable many-to-many fields; to-one sets use readable public identities or null, and many-to-many `set`/`choose` values are lists of readable public ids (empty clears); one-to-many stays refused. Values and field
editability are validated when asking. Multiple alternatives cannot write the
same field of the same record. A record action may call only a method named by
the model's [`DecisionTargetMixin`](mixins.py) `decision_methods` tuple; the proposal's keyword
`arguments` are bound to the method's signature when the request is validated, so
a method with required parameters (a strict `cancel(reason)`) is a valid call
when the proposal names them and refused when it does not. `delete` is
never permitted. JSON null values are retained.

`decision.decide(actor=actor, chosen=keys, revision=revision)`
validates distinct offered keys and the single/multiple choice rule, stores the
verdict in authored order, increments the optimistic revision and emits
`decision_answered` under the answerer's actor inside the answer transaction.
Non-workflow askers apply there, so refusal rolls the verdict back. Workflow
subscribers schedule their wake after commit and apply when the step resumes. The Python revision is optional; the card's
GraphQL mutation requires its observed revision. Invalid choices write nothing.
An answer may be given for a represented record of any model, such as a party who
answered by phone: `decide(..., on_behalf_of=EvidenceReference.of(record),
evidence=(EvidenceReference.of(file),), note="...")`. The references name a model, a
public id and an optional pointer, nothing more. Every participant (the answerer,
requester and active assignees) must read each record, as asking requires of the
question's context, or the answer is refused; `answered_for` and `answer_evidence` are
then rebuilt from the resolved records, so no caller-written label is stored.
`answer_note` keeps the note. `answered_by` stays the actor and `act` still decides; the
represented record grants nothing. Each malformed argument is refused under its own key.
GraphQL shows `answered_for` and each `answer_evidence` reference only to a reader who
may read that record (`provenance.evidence.readable_evidence`), so a later reader of the
question learns nothing about records it cannot read.
`decision.withdraw(actor=actor, reason="...")` closes a question with an empty verdict
in the asking owner's system context and keeps the reason as `withdrawal_reason`.
Decisions cannot tell askers apart: any named system context counts as the asking
owner's, for `withdraw` and `supersede` alike.
`Decision.objects.supersede(decision, request, actor=actor, reason="...")` replaces an
open question in one transaction, again only in the asking owner's system context: it
asks `request`, withdraws the old question with the reason and links the replacement as
`superseded_by`, so one question stays open and a stale answer to the old one meets the
revision refusal. A question a requesting record retains (`requester_querysets`, a
workflow step's) belongs to that asker, so supersede refuses it. Escalation is a
supersede naming the escalation's assignees; no question changes in place.
`decision.replacement_request(due_at=, remind_after=, assignees=None)` derives that
request from the stored question (kind, concerned records, proposal, context,
requester) in the asking owner's system context. The instants are required and never
copied, so a past reminder is not replayed; escalators never rebuild a request from the
stored JSON. Decisions runs no due-time sweep: an escalating addon receives
`decision_reminder_due` and supersedes there.

A request may carry `due_at` and `remind_after`, aware instants the asker supplies;
decisions does no date arithmetic on them and they stay fixed once asked. Overdue (open,
with a `due_at` that has passed; an undated question never is) has one owner, derived
when read: `Decision.objects.overdue()`, the `is_overdue` property, and the resource's
`is_overdue` output field and filter. Consumers read it rather than compare `due_at`.
`decision.remind()` stamps `reminded_at` once as decisions' own system work, then sends
`decision_reminder_due` inside the transaction but outside that elevation, and returns
whether it did; the revision does not move. Receivers hold no authority from decisions:
one that delivers the reminder opens its own named system context for the reads it
needs, and its error rolls back only that question's reminder. The `decisions.remind_due`
beat task (every minute, expiring after one tick) calls `remind_due(now=, limit=200)`,
which skips rows another sweep holds and reminds each due open question in its own
savepoint, so a failing receiver never starves the rest. A question a requesting record
retains keeps its asker's own timing and is skipped. Delivering a reminder belongs to an
addon above decisions, after commit. The record timeline shows the
reminder as a `decisions.reminder` line beside its question's line.

Decisions execute no proposal actions. The asker consumes the answer through the
records' owners. Free correction means editing the record and then choosing the
alternative that keeps its current values.

`DecisionContext` supplies a short reason, typed facts and evidence references for the card.
The question and answer controls precede compact supporting facts. Facts may
declare a registered read widget and its sibling row context, so formatting
stays with the app's widgets rather than the decision renderer.
Facts describe evidence; proposed fields live only in alternatives' actions.
The models are `Decision` and `DecisionRecord`.

`Decision.objects.open_for(*records)` is a normal REBAC-scoped open queryset through
concern links. The attention expression uses `Exists` over all open questions
concerning the readable records. Attention counts questions for anyone; the question's
own read scope still governs cards and answers.

Installed decisions contributes `has_open_decisions` to every Hasura model
resource through `ANGEE_GRAPHQL_RESOURCE_FILTERS`. Strawberry adds the annotation
only when selected, and the resource owner prepares its filter only when requested
(including nested Boolean predicates). Lists that use neither pay no Decision
permission compilation. There is no custom expression or separate scoping path.

The [web fragment](web/README.md) exports `DecisionCard`,
`decisionFieldMarks`, `attentionColumn` and `openDecisionFilter`. Cards show each alternative and its per-record
changes, select radio buttons or checkboxes, and display the chosen verdict and
answer attribution after closure. The `decisions#origin` seam is supplied by
askers.
