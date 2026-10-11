# Provenance

This addon stores the swappable Changeset metadata used by
[`angee.base.changesets`](../../../angee/base/changesets.py). Source declarations
are abstract; the composer emits the concrete model. An addon composing history
declares provenance in its own manifest.

`changeset()` reads REBAC's current actor and sudo reason. It grants no bypass,
and records its metadata only when a captured write occurs. Empty request and
command boundaries require no database table. Nested boundaries join the outer
changeset. Request middleware runs after REBAC's actor middleware; tasks and the
host's management entrypoint open system activities without granting a bypass.
The model is not history-tracked.

The serialized actor is attribution data. A native actor relationship tuple
grants that actor read access; platform admins may also read. Integration actors
use their canonical `integrate/integration` reference. Rule and seed sources use
the registered `provenance/rule` and `provenance/seed` subject types. The owning
source chooses its stable subject ID; these types require no Source table.
History surfaces will authorize reads through the touched record's own read
permission. The changeset index itself does not grant access to touched records.

`ANGEE_CHANGESET_MODEL` follows Django's swappable-model convention. Its model
must supply the metadata fields declared by Changeset. The `cst_` sqid prefix
names the default implementation. Export hashing, revert, archives and tree heads
are subsequent capabilities; the reserved hash column is currently empty.
