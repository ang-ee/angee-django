# Decisions

A decision asks people a question through a frozen form. Each decision is one
seat with its own assignees, requester, actions, and evidence. Groups settle
according to their registered policy; a closed answer remains an audit fact.

Human admission requires existing read access to the subject and evidence. A
named system context may admit a question without a human issuer or requester.
With `assignees=None`, a consumer's declared REBAC relation owns live assignment;
an empty explicit assignee list remains invalid. Delegated questions retain no
evidence without explicit participants whose standing access can be checked.
Admission creates no grants. The requester cannot act on their own request unless they hold the
administrative role, and every resolver must remain an active person.

Any waiting owner can retain a group reference and observe its settlement
signal, with a sweep over settled groups as its durable recovery path. Decisions
own admission, deciding, expiry, cancellation, supersession, and evidence
protection independently of the waiter.

`DecisionRequest.replaces` retains a successor link even for an answered seat;
supersession never changes that answer. Historical data migrations may use the
`imported` closure for answers with no known resolver or resolution time.

The console inbox exposes `decisions`, `decision_groups`, and
`decision_evidence`. `HumanDecisionType`, `HumanDecisionVerdict`, and
`decide_human_decision` coexist with the workflow addon's distinct decision API.
The mutation dispatches through `Decision.decide`; a model donor can compose a
domain transaction there and delegate the final transition to the manager.
Intake uses this seam so inbox answers obey the same account-linking checks as
`Need.decide_access`. All answer writes remain conditional under the group lock.


The `@angee/decisions` web addon renders each frozen action through the shared
[FormSpec and record action owners](../../../packages/ui/src/views/form/), while
`decisionRecordTab(model)` and `DecisionsList(baseFilter)` compose the decisions
resource for a subject. The resource's own read scope and filter axes own those
reads. Revisit dispatches through `Decision.revisit` with the expected revision;
the subject donor owns eligibility and successor admission.
