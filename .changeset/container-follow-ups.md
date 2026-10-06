---
"@angee/app": major
"@angee/ui": major
---

Containers follow-ups.

- **Sections and the rail resolve per row.** `form#sections` and `form#rail` accept `impl` children and variants, as the record verbs do. The record projection reads every candidate's fields, and each record shows the children it admits: its implementation's children, and a variant in its original's place, which falls back to the original when the row lacks the variant's permission.
  - Groups, tabs and section actions from those children follow the row.
  - The form's layout (title, status, body) and its save read only the fields of the children the record admits. A create form, which knows no implementation, leaves out the children that need one, along with their required fields and defaults.
  - An original and its variants may reuse a tab or rail group id, never a fixed group's.
  - The projection (`resolveContainer(…, { projection: true })`) places an unplaced variant right after its original.
- **`when: { app }` matches the page's whole app trail.** That is every app on its menu trail, outermost first, flattened ones included. With an app selected, a page outside its rail sits in the selection's home root alone. A condition must name such an app; a plain menu item id now fails at boot.
- **The deployment may force (G-14).** A deployment `only` still narrows like any other. With `force: true` beside it, it replaces every addon layer's `only` and `except` on that container (across the addresses a page merges) or that menu node, so a deployment can admit what an addon narrowed away.
  - Only the deployment may force, and only beside an `only`; addon layers still only narrow.
  - A `hide` stays until it is shown again.
  - Developer mode labels the rule "force only".
- **`TopBar` keeps only the props the console passes:** `navigation`, `primaryPane`, `chatterPane`, `showChatterToggle`, `showUserMenu` and `className`. `brand`, `hideSearch`, `searchPlaceholder`, `onHelp`, `onNotifications`, `trailing` and `children` are removed. Nothing passed them, and addons reach the top bar through the shell containers. `Systray` remains available for a future contribution.
