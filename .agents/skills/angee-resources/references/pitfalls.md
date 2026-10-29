# Resource pitfalls

Moved from the backend guide. The loader contracts stayed there:
[Resource loading](../../../../docs/backend/guidelines.md#resource-loading) and
[Migration history](../../../../docs/backend/guidelines.md#migration-history) (retiring seeded rows).

- **Seeded rows selected by clients carry a resource-assigned stable key.**
  A lookup by display name breaks on the first rename or translation; the
  [resource loading contract](../../../../docs/backend/guidelines.md#resource-loading)
  names the stable key as the only client-side selector.
- **A resource yaml loads only when listed** in the addon's `pyproject.toml`
  `[tool.angee.resources]` manifest (`{tier = [paths]}`); an unlisted file silently
  loads nothing.
