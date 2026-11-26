---
"@angee/ui": patch
---

A row whose implementation key the registry no longer names reads that column as
null, and the console says so instead of showing an empty value: a list cell of
an implementation-key column (one the resource's `implFields` names) reads "Not
registered" (`implementation.notRegistered`) through the status badge, in the
warning tone. On the record's own form the field carries "Not registered" as an
attention mark while its control still offers every registered implementation:
from the stored record (a create form never shows it) and while the form holds
no other choice (picking a registered one clears the mark until the draft is
discarded). A dialog's field of the same name carries none. A null in any other
column, or in a column reached through a relation (`channel.backend_class`),
renders as before.

Not changed: a list grouped by such a column, a board laned by it and a facet
counted on it read the column's groups, which fail for such a row, as before:
the grouping library refuses to shape a group key from a stored value outside
the column's choices (`ChoicesValueNotInEnumError`). A filter chip offers only
registered implementations.
