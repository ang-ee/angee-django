# Proposals record contributions

The web addon contributes round ceremonies and the People roster to Round and
Project records. `active_proposal_round` selects the newest readable collecting
or opened round directly targeting the project, targeting its source Task, or
owning its proposal track. Ordering is creation time followed by primary key.
The selector returns a queryset so Strawberry applies capability annotations
and roster prefetches before choosing the row.

Open uses the returned write permission and `can_open`; admission uses
`can_admit`. Other ceremonies retain `useRoundCeremonyActions` permissions and
revision guards. `holdsPermission` and the ceremony hook are public exports.
People reuses those descriptors for Admit and row-specific Remove. Project's
Approach tab composes `RoundComparisonGrid`; audience labels occur once in each
column header, and answer cells contain only the answer.

`AnswerVisibility` binds the registered UI visibility widget to the answer's
`allowed_visibility` projection and revision-checked verb. Responders may narrow
the current audience; managers may also widen it. Task publication constraints
contribute through the projects owner's `visibility_blockers` seam.
