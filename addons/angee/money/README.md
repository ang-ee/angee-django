# Money

Global currency rates derive shared reads from their context and reference
columns through the [permission policy](permissions.zed). Contextual rates keep
their independently granted access. The
[filtered-constant convention](../../../docs/backend/guidelines.md#rebac) owns
the shared-reader contract.

Money roles use explicit memberships and manager bindings so rate read scopes
compile to SQL.
