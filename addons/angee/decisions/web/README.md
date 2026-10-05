# Decisions web

`DecisionCard` shows the question, concerned record links, context, proposal
alternatives and their changes. One alternative uses radio buttons; a proposal
with `multiple` uses checkboxes. The card submits chosen keys and the observed
revision through the generated decision mutation. A closed card displays the
selected alternatives, `answered_by`, and `answered_at`.

The inbox uses the resource owner's list, filter and routing components.
The workflow owner's `RecordTimeline` embeds the same card beside a record or
record set. Askers contribute origin information through `decisions#origin`.

`decisionFieldMarks` supplies the form bridge's unconfirmed marks from open
questions' alternative field writes.
`decisionAttentionColumn()` and `openDecisionFilter` opt a list into the derived
boolean attention badge and filter without requesting a count query.
A free correction is a direct record edit followed by choosing an alternative
without actions. Decisions supplies no answer input form.
