---
"@angee/app": major
"@angee/ui": major
---

Containers replace slots and surface. An addon's `containers` dict keys
addresses (`node#name`): an own-namespace key declares a child, any other key
alters a dependency's child, and `only`, `except` and `when` (app, route)
narrow what renders, layered by addon dependency like menus. The
name after `#` types the entry through `ContainerKinds`.

- Framework containers: `form#sections`, `#rail`, `#actions`, `#actions-menu`
  and `#chrome` (with model addresses that inherit along MTI parents);
  `resource#views` (contributed view kinds beside the built-in five) and
  `#utilities`; `record#aside` (chatter); `shell#notices`, `#user-menu`,
  `#drawers-right` and `#drawers-bottom`; `auth.login#method` and
  `#password-help`. An addon declares a container on its own node, model-scoped
  with `models: true` or one child per key with `unique: "key"`.
- A child may carry `permission`, `requiredFields`, `impl` (shown only on that
  implementation's rows) or `variant: { of, impl }` (stands in for another child
  on those rows, falling back to it when the row lacks the variant's permission).
- `@angee/ui`: `useContainer`, `resolveContainer`, `containersFromChildren`,
  `ContainerOutlet`, `useDrawers(edge)`, `ChatterTabContent`,
  `ResourceViewKindContent`, `RESOURCE_CONTAINERS`, `FORM_CONTAINERS`,
  `SHELL_CONTAINERS`, `CHATTER_CONTAINERS`. `ResourceViewKind` admits
  namespaced contributed kinds. Chatter tab ids are namespaced, with their old
  ids kept as `?chatterTab=` aliases for one release.
- `@angee/app`: `LOGIN_CONTAINERS`, the `containers` and `dashboardStore`
  manifest keys (composed by `composeAddons`) and `ANGEE_UI.containers`;
  developer mode lists container narrowing, removals and provenance.
- Removed: the `slots`, `surface`, `chatter` and `drawers` manifest keys,
  `createApp`'s `slots` input, `useSlot`, `useModelSlot`, `SlotContribution`,
  `ModelSlotTarget`, the form, console, user-menu, login and resource-utilities
  slot constants and helpers, `recordActionPlacement`, `SlotOutlet` (now
  `ContainerOutlet`), the surface policy, `createLayoutSlot`, the login card and
  page footers, and `implementation.detail`.
