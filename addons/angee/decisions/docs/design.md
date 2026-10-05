# Decisions on records: design

Design, 2026-10-04. Implemented by decisions and the shared workflow timeline as described below. Companion: [workflows design](../../workflows/docs/design.md).

## Ontology

What exists and how it relates, independent of any code.

- **Record:** a row of any model. **Field:** a named property of a model.
- **Decision:** a question that must be answered, by a human or by an agent. It has a kind, a proposal, those
  who may answer, and a state: open or closed.
- **What a decision concerns:** records. A decision relates many-to-many to the records it applies to, so the
  decisions about a record are easy to find.
- **Proposal:** the alternatives a decision offers. Each alternative says what happens if it is chosen: the
  actions on the concerned records (set this field to this value, do this to the whole record) and how the asker
  continues.
- **Attention:** a record needs attention when at least one open decision concerns it, whoever may answer it. A field is unconfirmed when
  an open decision about its record proposes an action on that field. Attention is derived; it is never stored.
- **Verdict:** the answer. It chooses one or several of the proposal's alternatives, with who answered, and
  closes the decision. The asker then applies the chosen alternatives' actions and continues as they say.

Rules:

1. A decision closes in two ways: someone answers it, or its asker withdraws it (a person stopped the workflow to
   do the work by hand). People can change records directly at any time; that does not answer a decision about
   them.
2. A value that awaits confirmation is written on the record. The open decision on that field is what marks it
   unconfirmed.
3. A decision gates whoever asked it. An owner that must wait asks one question: does any open decision concern
   this record?
4. A decision does not change records and does not know about flags. Flags are tags: records that workflow steps
   or people create and delete. A step may set one because of an answer.
5. Who asks a decision (a workflow step, a person, another addon) is not part of the decision's meaning.

Examples: "confirm the sender" proposes a value for one field of an imported document; "are these the same document?" concerns
two records; "send this reply?" concerns a whole message.

## Implementation

How the ontology maps to this addon.

- **One model, `Decision`:** `kind`, `requester`, `assignees` (users or agents who may answer), `records`,
  `proposal`, `context` (evidence shown on the card), and the verdict: `verdict` (the chosen alternative keys),
  `answered_by`, `answered_at`. A decision is open while it has no verdict. There is no group, no policy, no
  round, no separate list of answer options and no input form: the alternatives are the proposal, and a free
  correction is a direct edit of the record followed by choosing the alternative that keeps the record as it is.
- **`Decision.records`** (new): a many-to-many to the records the decision applies to (a through row of
  decision, content type and id). It exists to find decisions by record; it carries nothing else.
- **`Decision.proposal`** (new, JSON): the alternatives, each with its actions per record and per field and the
  outcome the asker continues with; `multiple` says whether several may be chosen:

  ```json
  {"multiple": false, "alternatives": [
    {"key": "confirm", "label": "Confirm the sender", "outcome": "matched",
     "actions": {"<record id>": {"fields": {"sender": {"set": "<party id>"}}}}},
    {"key": "create", "label": "Create a new sender", "outcome": "create_sender"},
    {"key": "keep", "label": "Keep what is on the record", "outcome": "matched"}]}
  ```

  Fields are names inside the JSON. No field table, no per-field rows.
  An action may name `model` for a concrete child owning those fields or methods.
  It must share the concerned record's canonical identity, and the asker supplies
  that concrete record. Concern links remain canonical; application locks and
  writes through the named model's permission owner. Omission uses the canonical
  model. The card uses the action model's field metadata.
  A model declares callable proposal methods once in its class-level
  `decision_methods` tuple. Ask-time validation requires a declared method with
  no required arguments, rejects `delete`, and checks optional keyword
  `arguments`. Field values, readable foreign-key identities and editability are
  validated when asking; multiple alternatives cannot overlap a field write.
- **Attention query:** one queryset helper and one GraphQL filter over `Decision.records`, usable on any model
  with no per-model declaration: records with open decisions, and the open decisions of a record.
- **Answering:** `Decision.objects.decide(decision, chosen keys, actor)` is the one entry, for humans and agents
  alike. It records the verdict; it applies nothing itself. The asker applies the chosen alternatives' actions
  through the records' own owners and continues along their outcome.
  Non-workflow askers apply inside the answer transaction and propagate refusal;
  workflow steps apply on resume and expose failure as an error hold.
- **Gate:** `Decision.objects.open_for(record)` is the one call other owners use.
- **Withdrawing:** the asker closes its own open decision with an empty verdict, recorded with who stopped it.
  "Open" stays one rule: no verdict yet.
- **No verdict decisions:** a decision is asked only when something needs judgement. The final action on a record
  (for example posting it) is the record's own action, not a decision.

UI, all in this addon's web fragment:

- **Decision card:** the question, the concerned records as links, the alternatives with what each would change. One component, used in
  the inbox, in the record timeline and next to a record set.
- **Field mark:** a form marks the fields named in the proposals of its record's open decisions; choosing the
  mark opens the card.
- **List:** any list can show an attention badge and filter by it.

Not built in the first version: default answers, deadlines, automatic answers, questions answered jointly by
several people.
