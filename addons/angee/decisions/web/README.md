# Decisions web

The routed `ResourceList` owns the inbox and record frame. Its editable default
filter selects open seats assigned to the current person. The server-owned
`can_act` filter finds seats they can answer, including delegated assignments;
the native filter box supplies assigned/requested/answerable and open/settled predicates
and saved views. The backend owns
visibility, authority, expiry, and settlement. A requester filter does not grant access, and the other
seats section shows only decisions the current person may read.
The rail entry is named Decisions; messaging retains Inbox. The backend's
`is_open` field/filter owns the open view, and the `permissions` list's `act`
value owns viewer editability.

The record declares a read-only `Form`. For a viewer who can act on an open
decision, the page renders the `Decide` form inline (the shared action form's
`inline` presentation) with the kind's registered content, and opens the
decision's subject beside it in the record peek on large screens; there is no
Decide dialog. Others see the content read-only. The form's dynamic args
use the shared `jsonSchemaActionArgs` owner for the retained JSON Schema,
including offered actions, initial values, immutable fields and validation.
The generated `decide` action receives the action separately from its values
and the expected revision.
Server field errors use the shared form-error owner. Invalid submissions advance
the backend revision; the page refreshes that token while retaining the draft.
A conflict locks the draft; reloading the page reviews the refreshed question.
Changing actions replaces only branch values; consumer-content values outside
those fields survive. Successful submissions use native model invalidation;
only rejected submissions refresh the revision directly.

Consumers contribute `decisionContent(kind, Component)` through their addon's
`slots` array. The helper targets the owned content slot, which accepts exactly one contribution per kind:
the framework's composition collision check rejects duplicate claims. Content
receives read-only `{decision, basis, context}` in the Context tab and in the
inline Decide form, where it inherits the action's native React Hook Form context.
The record tab disables consumer controls. Consumers parse their own basis/context
payloads before using them. When a kind has registered content, the generic Facts
region stays hidden to avoid duplicating its presentation. Otherwise the generic
context renderer validates and displays facts as readable fields; references and
evidence open through record peeks.

`DECISION_ORIGIN_SLOT` lets an independent waiting owner contribute links for the
current group. Its content reads `useDecisionContent()` and renders nothing when
that owner has no relevant link. The page renders nothing for an empty slot.
Decisions imports no waiting owner's concepts or queries.

`Decision.kind_label` owns the inbox label and record representation. The Context
tab lists other visible seats through the same resource list owner.
Consumers can contribute `decisionRecordTab(model)` to show decisions for a
record; its filter uses the backend's `subject_model` and `subject_id` fields.

This schema-dependent fragment typechecks after composition and web codegen at
the stack root. Its stories and provider-backed tests exercise the same shared
controls as the page.
