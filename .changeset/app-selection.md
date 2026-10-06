---
"@angee/app": major
"@angee/ui": major
---

The deployment selects the app by hostname or `?app=`; an app root declares its
own home, brand and theme.

- **App roots** gain `home` (a route name inside the root), `brand`
  (`{ name, mark }`, defaulting to the root's label and icon) and `theme` (an
  installed theme id) as menu fields, in the dict and the legacy list. They
  layer like any menu field; an included app drops them.
- **`ANGEE_UI.shell`** is `{ brand?, theme?, apps?: { <name>: { rail, brand?,
  theme?, home? } }, hosts?: { <hostname>: <root id | apps name> } }`. The
  composer checks its shape. `createApp` checks every root, app and host at
  boot, selected or not: a rail lists top-level roots, an app name is no root
  id, a home lies inside its rail, a mark is registered and a theme installed.
- **`selectApp({ search, hostname, shell, roots })`** takes `?app=`, else the
  hostname's `hosts` entry, else nothing, which shows every root with
  `shell.brand` and `shell.theme`. A root id is the one-root app; a named app
  takes its own brand, theme and home, else its first rail root's. An unknown
  `?app=` warns and falls back. `createApp` takes `location` (default
  `window.location`).
- **The selection shapes navigation, never access.** The rail, command palette
  and Refine resources follow the selected rail plus the personal Settings
  roots, and `/` lands on the app's home. Pages outside the rail stay reachable
  and sit in the selection's home root: no route is guarded, and only a menu
  `remove` makes a page unavailable. The runtime brand (rail, sign-in, document
  title) and the appearance's default theme come from the selection; a person's
  own theme still wins.
- **Removed:** the `shell`, `perspectives` and deprecated `brand` addon keys
  (only the deployment layer carries `shell`); `resolveShell`,
  `ShellDeclaration`, `PerspectiveDeclaration` and `ResolvedShell`;
  `createApp`'s `home` and `confineTo`; `when: { perspective }` and
  `ContainerScope.perspective`; the `appRoot` menu field and
  `MenuTree.appRoots()`. `AppRuntime.confineTo` becomes `rail`,
  `RuntimeComposition.shell` and `effective` become `selection` and `home`, and
  `MenuTree.confineTo` takes a list of root ids.
