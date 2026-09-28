# Money

Global currency rates derive shared reads from their context and reference
columns through the [permission policy](permissions.zed). Contextual rates keep
their independently granted access. The
[filtered-constant convention](../../../docs/backend/guidelines.md#rebac) owns
the shared-reader contract.

Money roles use explicit memberships and manager bindings. The recursive
`money/role#includes` relation is removed so rate read scopes compile to SQL.
Replace any role-inclusion grants with the intended explicit memberships or
bindings before upgrading.

The [cleanup migration](runtime_migrations/shared_reader_cleanup.py) removes
retired `money/rate#shared` user-wildcard tuples and all `money/role#includes`
tuples from both relationship stores during `migrate`, without prior permission
sync. Other shares, memberships, bindings, and resource-registry rows remain.
Sync the updated permission schema afterwards.
