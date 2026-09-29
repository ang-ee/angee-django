# Proposals record contributions

The web addon contributes round ceremonies and the People roster to Round and
Project records. `active_proposal_round` selects the newest readable collecting
or opened round directly targeting the project, targeting its source Task, or
owning its proposal track. Ordering is creation time followed by primary key.
The selector returns a queryset so Strawberry applies capability annotations
and roster prefetches before choosing the row.

Open uses the returned write permission and `can_open`; admission uses
`can_admit`. People composes the same ceremony descriptors for Admit and
row-specific Remove. Both comparison surfaces use one body and display
Visibility once per proposal column. Answer forms declare the shared
[visibility field widget](../../../packages/ui/src/widgets/visibility.tsx), bound
to `allowed_visibility` and the revision-checked verb; the projects owner
provides Task publication blockers.
