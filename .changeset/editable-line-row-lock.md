---
"@angee/metadata": minor
"@angee/ui": minor
---

Editable lines honour a backend row lock. Lines metadata gains `lockField`, the child
node's list of the fields a row's own state locks, and line reads select it. A locked
row is a system row in `EditableLines`: it shows a lock where its reorder handle would
be, carries a "System" marker, offers no duplicate or remove, and its locked cells are
read-only while its other cells stay editable. `RowsListView` gains `canReorderRow`: a
row that may not move shows a lock instead of its handle and never starts a reorder.
