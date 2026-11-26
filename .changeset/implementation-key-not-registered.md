---
"@angee/ui": patch
---

A row whose implementation key the registry no longer names reads that column as
null, and the console says so instead of showing an empty value: a list cell of
an implementation-key column (one the resource's `implFields` names) reads "Not
registered" (`implementation.notRegistered`) through the status badge, in the
warning tone. On the record's own form the field carries "Not registered" as an
attention mark, from the stored record (a create form never shows it) and while
the form holds no other choice (picking a registered one clears the mark until
the draft is discarded). The mark explains itself, as its field's accessible
description (and its own, where the mark is the keyboard's stop) and on hover:
where the field offers every registered implementation, "Its implementation
cannot run until another is chosen."; where it is read-only on that form
(declared `readOnly`, `createOnly` on a stored record, a column the update does
not write, or a person who cannot write the record), "Its implementation cannot
run. It cannot be changed here." The record's other fields save either way; a
refused choice shows the server's reason under the field. A dialog's field of
the same name carries none. A null in any other column, or in a column reached
through a relation (`channel.backend_class`), renders as before.

A list grouped by such a column, a board laned by it and a facet counted on it
still cannot be read for such a row: the server refuses, publicly and in words
naming the column, and the list and the board show those words once, without
retrying, while the search panel shows them in that facet's own section and the
other facets keep their counts. There is no "Not registered" group, lane or
facet bucket yet. A filter chip offers only registered implementations.
