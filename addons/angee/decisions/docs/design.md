# Decisions on records: design

Design, 2026-10-04. Not implemented yet. Companion: [workflows design](../../workflows/docs/design.md).

## Ontology

What exists and how it relates, independent of any code.

- **Record:** a row of any model. **Field:** a named property of a model.
- **Decision:** a question that must be answered, by a human or by an agent. It has a kind, a set of possible
  answers, those who may answer, and a state: open or closed.
- **What a decision concerns:** records. A decision relates many-to-many to the records it applies to, so the
  decisions about a record are easy to find.
- **What a decision proposes:** its detail lists the proposed actions, per record and per field: set this field
  to this value, or do this to the whole record.
- **Attention:** a record needs attention when at least one open decision concerns it. A field is unconfirmed when
  an open decision about its record proposes an action on that field. Attention is derived; it is never stored.
- **Answer:** the chosen option, with who answered. Answering closes the decision. What happens next belongs to
  whoever asked: a waiting workflow continues and its steps act on the answer.

Rules:

1. Only an answer closes a decision. People can change records directly at any time; that does not answer a
   decision about them.
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

- **`Decision`** keeps kind, answers, assignees (users or agents), state and audit. It loses its single subject
  (`subject_content_type` and the record reference on `Decision`, `models.py`).
- **`Decision.records`** (new): a many-to-many to the records the decision applies to (a through row of
  decision, content type and id). It exists to find decisions by record; it carries nothing else.
- **`Decision.proposal`** (new, JSON): the proposed actions, per record and per field:

  ```json
  {"<record id>": {"fields": {"sender": {"set": "<party id>"}, "due_date": {"set": "2026-11-14"}},
                   "record": {"call": "cancel"}}}
  ```

  Fields are names inside the JSON, read by the form to mark them and by the asker to apply the answer. No field
  table, no per-field rows.
- **Attention query:** one queryset helper and one GraphQL filter over `Decision.records`, usable on any model
  with no per-model declaration: records with open decisions, and the open decisions of a record.
- **Answering:** `Decision.objects.decide` stays the one entry, for humans and agents alike. It records the answer
  and closes the decision; it applies nothing to the concerned records.
- **Gate:** `Decision.objects.open_for(record)` is the one call other owners use.

UI, all in this addon's web fragment:

- **Decision card:** the question, the concerned records as links, the proposed actions per field, the answers. One component, used in
  the inbox, in the record timeline and next to a record set.
- **Field mark:** a form marks the fields named in the proposals of its record's open decisions; choosing the
  mark opens the card.
- **List:** any list can show an attention badge and filter by it.

Not built in the first version: default answers, deadlines, automatic answers.
