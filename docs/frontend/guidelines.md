# Frontend Guidelines

Frontend code is TypeScript, React, and the rendered Angee experience. It owns
presentation, routes, menus, widgets, shells, view state, and interaction.

Follow the shared development process and coding principles in
[`docs/guidelines.md`](../guidelines.md) for every task; the rules below are the
frontend-specific layer applied during the Build step.

## Stack

The opinionated stack in `docs/stack.md` is the source of truth for frontend
libraries and what each one owns. Check it before adding a dependency or
hand-rolling a concern. TypeScript dependency setup belongs in `package.json`,
`pnpm-workspace.yaml`, and `pnpm-lock.yaml`.

## Rules

- Python ships schema and operations. TypeScript ships UX.
- React does not own business logic, permissions, models, or persistence.
- Use `defineAddon` for addon contribution and `createApp` for the project's
  host composition.
- One component tree. Extend or register; do not fork.
- Slots are additive extension points. Use them before copying a component.
- Tokens beat color props and one-off variants. Theme by overriding tokens.
- Use shared page, view, form, table, widget, and shell primitives before adding
  new local state.
- Client-side gates are UX only. The server is the authorization boundary.
- No Python view DSL, no frontend metadata hidden in backend decorators.

## Checks

Run package-scoped commands while editing, then the broad checks before handoff:

```sh
pnpm run typecheck
pnpm run test
pnpm run build
```

Use browser verification for meaningful UI changes.
