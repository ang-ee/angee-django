---
"@angee/ui": patch
---

A record form confirms a save as soon as the write answers. After a successful
write the save awaited the refresh of every dependent resource (`invalidateModels`)
and of every authored read registered on the saved model, among them the reads
the record's own chrome mounts on every saved form (provenance marks, the
timeline's feed, the follow thread, the readers roster). One slow read delayed
"Changes saved" by as long as it took; one that failed was retried first,
about seven seconds under the app's query defaults; one that never answered
held the confirmation for ever, and until it settled Save sent no further
write, so the form could not be saved again. The form itself had already
turned clean, so the save looked hung.

The save now starts those refreshes and does not wait for them. What the form
shows is its own record, which the write's answer carries. A read that fails
reports through its own query, as before.

What changes for a reader: a list of a dependent model, or an authored read of
the saved model, may show the saved state a moment after the confirmation
where it showed it just before. The saved model's own lists already refreshed
after it. A save that writes lines still waits for its own model's lists. A
record action and a dialog still wait for their refreshes (`useAuthoredMutation`).
