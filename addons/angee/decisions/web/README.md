# Decisions web

`DecisionCard` shows the question, concerned record links, context, proposal
alternatives and their changes. One alternative uses radio buttons; a proposal
with `multiple` uses checkboxes. The card submits chosen keys and the observed
revision and `values={record_public_id: {field: value}}` through the decision mutation.
A closed card names the selected alternatives and supplied values from `verdict_values`.

The inbox uses the resource owner's list, filter and routing components.
The workflow owner's `RecordTimeline` embeds the same card beside a record or
record set. Askers contribute origin information through `decisions#origin`.

`decisionFieldMarks` supplies the form bridge's unconfirmed marks from open
questions' alternative field writes.
`decisionAttentionColumn()` and `openDecisionFilter` opt a list into the derived
boolean attention badge and filter without requesting a count query.
A free correction is a direct record edit followed by choosing an alternative
without actions. A field action is `{}`, `{"set": value}`, or `{"choose": {}}`.
Selecting an alternative with `choose` reveals that model field's standard widget
through `@angee/ui`'s `FieldDescriptorControl`: form metadata defaults resolve
scalars and `RelationFieldWidget` supplies server search and metadata-backed inline
create. `choose.filter` optionally supplies a Hasura condition, such as
`{"name": {"_neq": "Hidden"}}`, combined with the picker's search. Inline create
saves immediately and selects the new public id. Many-to-many remains refused by
the backend. Confirm requires a value for every selected `choose` field; changing
the revision clears both keys and values. Decisions supplies no separate answer form.
