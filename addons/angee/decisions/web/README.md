# Decisions web

The inbox separates a person's assigned seats from their readable requests and
lets them return to settled decisions. The backend owns visibility, authority,
expiry, and settlement. A requester filter does not grant access, and the other
seats section shows only decisions the current person may read.
The rail entry is named Decisions; messaging retains Inbox. The backend's
`is_open` field/filter owns the open view, and `can_act` owns viewer editability.

The decision page uses the retained JSON Schema, including its offered actions,
initial values and immutable fields. FormSpec supplies controls, Ajv validates
the selected action, and React Hook Form owns the draft. The generated `decide`
action receives the action separately from its values and the expected revision.
Server field errors use the shared form-error owner. Invalid submissions advance
the backend revision; the page refreshes that token while retaining the draft.
A conflict retains the draft and requires an explicit reload.
Changing actions replaces only branch values; consumer-content values outside
those fields survive. Successful submissions use native model invalidation;
only rejected submissions refresh the revision directly.

Consumers contribute `decisionContent(kind, Component)` through their addon's
`slots` array. `DECISION_CONTENT_SLOT` accepts exactly one contribution per kind:
the framework's composition collision check rejects duplicate claims. Content
receives read-only `{decision, basis, context}` and inherits the page's native
React Hook Form context. Consumers parse their own basis/context payloads before
using them; the generic context renderer validates the decisions-owned facts,
references and evidence shape and opens references through record peeks.

`DECISION_ORIGIN_SLOT` lets an independent waiting owner contribute links for the
current group. Its content reads `useDecisionContent()` and renders nothing when
that owner has no relevant link. The page renders nothing for an empty slot.
Decisions imports no waiting owner's concepts or queries.

The existing record chrome composes on this page. Subject-record settlement
history awaits a subject-filtered backend read and an additive activity-feed
contract; the current messaging Activity tab exposes neither. This fragment
does not register a competing activity implementation.

This schema-dependent fragment typechecks after composition and web codegen at
the stack root. Its stories and provider-backed tests exercise the same shared
controls as the page.
