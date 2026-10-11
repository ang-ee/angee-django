# Directory synchronization

A Directory composes the [record-sync driver](../integrate/README.md). Each
address book maps to a Folder and contacts stream; RecordLink owns external
identity and wire payloads. Party rows remain independent of any one directory.

The [CardDAV backend](../parties_integrate_carddav/backend.py) compares the shared
[contact projection](backends.py); wire properties outside that projection stay
in RecordLink history and survive conditional writes.

[PartyManager.ingest_contact](managers.py) asserts directory facts; Address and
Relationship keep their own identities and statements. Missing members withdraw
only that source. Canonical scopes retain unasserted rows and exclude trash and
wholly withdrawn rows. [Handle producers](managers.py) compose `Party.statements`
through the shared Handle, Party lock order; resolution owns the cross-party
winner and derived counters outside Party history.

Ordinary person edits assert normal statements; confirm/dismiss writes preferred
statements without rewriting the producer or copying evidence. Person preferences
survive remote removal: confirmed handles can be republished; dismissal makes
removal stick. Revert restores local state; sync retains its remote preconditions.
Consumers declare domain aspects; losing statements stay inspectable, and normal
handle statements below the Party's review threshold remain pending review.

Review and Identity read through the Party, with independent Handle and evidence
gates. Once asserted, subjects leave active use only through trash;
retained statements protect their subjects and Handle evidence from deletion.
Circle and spaces memberships keep plain acceptance/rejection decisions.

Source admission follows RecordLinks and seeded Rule/Seed role membership.
Sources and migrated carrier readers gain no private reads. Imports supply their
Integration and observed links; parties binds targets in the application transaction.

Identity matching writes normal RULE same_as statements on the ordered Party
pair, skipping current assertions and dismissals before each run's cap without
continuation state. A preferred no_value is the symmetric anti-link; preferred value
awaits a person's survivor choice. `survivor.merge` owns the changeset and redirect;
pointing owners union statements, retaining collisions as superseded.

The [declared folds](addon.toml) preserve proven source and person decisions;
unproven attribution blocks retirement by row. Migration docstrings own the
retained evidence, discarded legacy data and reversal contracts.
