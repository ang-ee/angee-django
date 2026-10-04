# Decisions web

`DecisionCard` shows the question, concerned record links, context, proposal
alternatives and their changes. One alternative uses radio buttons; a proposal
with `multiple` uses checkboxes. The card submits chosen keys and the observed
revision through the generated decision mutation. A closed card displays the
selected alternatives, `answered_by`, and `answered_at`.

The inbox uses the resource owner's list, filter and routing components.
`RecordDecisions` reads open questions for the selected record; `DecisionsList`
embeds a caller's readable selection. Askers contribute origin information through
`decisions#origin`.

`fieldsToMark(decisions, recordId)` returns sorted unique field names from all
alternatives' actions for that record, considering only verdict-null questions.
A free correction is a direct record edit followed by choosing an alternative
without actions. Decisions supplies no answer input form.
