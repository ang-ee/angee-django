# Decisions

A decision asks people a question through a frozen form. Each decision is one
seat with its own assignees, requester, actions, and evidence. Groups settle
according to their registered policy; a closed answer remains an audit fact.

Human admission requires existing read access to the subject and evidence. It creates
no grants. The requester cannot act on their own request unless they hold the
administrative role, and every resolver must remain an active person.
System admission may leave assignees delegated to a consumer's live `eligible`
permission, but only when a current domain actor can answer; the administrative
override does not establish eligibility. Such seats retain no extra
evidence without explicit readers. A group can be re-asked after settlement;
its earlier answer stays final and the successor group links through
`reasked_from`. Every resolved answer retains its resolver and resolution time.
The shared base evidence owner checks frozen references at admission. An action
class owns its `key`, typed Pydantic form and verdict; the decisions addon
registers it and its group policies through the base implementation registry.

The inbox owns the form and its React Hook Form context. Actors answer on the
decision page: the answer fields render inline beside the subject's peek, and
Decide sits in the record toolbar. A consumer contributes
one `decisionContent(kind, Component)` presentation per decision kind through
the decisions content slot; its retained basis and context arrive as read-only
payloads for the consumer to parse. A waiting addon contributes a separate
origin link without making decisions depend on that waiter.

Any waiting owner can retain a group reference and observe its settlement
signal, with a sweep over settled groups as its durable recovery path. Decisions
own admission, deciding, expiry, cancellation, supersession, and evidence
protection independently of the waiter. Re-asked groups form a protected chain
so a retained round remains available to the waiter and to authorized run
operators through the workflow permission extension.
The GraphQL decision exposes `permissions` for `act`; its `decide` mutation
dispatches through the decision instance so a consumer can wrap the answer in
its own transaction.
