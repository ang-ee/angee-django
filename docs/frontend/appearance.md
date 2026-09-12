# Appearance and theme addons

Angee treats a visual design as an installed addon. A theme addon contributes
one or more named implementations; Appearance is the runtime setting that
selects one of those implementations, its light/dark behavior and any bounded
options it declares. Templates remain scaffolds or content structures.

The renderer has an intrinsic base token set even when no theme addon is
installed. `angee.theme_stock` makes that unchanged look selectable as
`angee.stock`. Installing another theme adds it to the catalogue and never
activates it by import order.

## Host and user precedence

Project and stack templates expose three build inputs:

- `appearance_theme_id`, which defaults to `angee.stock`;
- `appearance_color_scheme`, one of `system`, `light` or `dark`;
- `appearance_options`, an optional JSON `{ "version": 1, "value": ... }`
  envelope for a configurable host theme.

The Vite configuration validates those values against the generated catalogue.
Changing a host default therefore requires the normal codegen and web build.
The synchronous head bootstrap applies that default, or a validated actor cache,
before the application entry loads.

A signed-in user's `iam.User.preferences.appearance` fields override the host
independently. The stored document contains stable IDs and versioned inputs:

```json
{
  "version": 1,
  "themeId": "angee.brand",
  "colorScheme": "system",
  "options": {
    "version": 1,
    "value": { "brand": "#5b5bd6", "accent": "#0d9488", "radius": "6px" }
  }
}
```

An absent field follows the corresponding host default. Selecting a different
theme replaces the old theme's options. Reset removes the complete appearance
slice. If a saved theme is later uninstalled, the UI uses the host or intrinsic
fallback and retains the unavailable ID so reinstalling the addon can restore
the selection.

The DOM uses `data-theme-id` for implementation identity and
`data-color-scheme="light|dark"` for the resolved scheme. `data-theme` mirrors
the scheme for one compatibility cycle.

## Authoring a theme addon

A model-less theme addon has the same `addon.toml` and web package structure as
other addons. Its package exports the browser contribution at `.` and a pure
Node-loadable definition entry at `./themes`:

```json
{
  "name": "@acme/theme-paper",
  "type": "module",
  "sideEffects": ["**/*.css"],
  "exports": {
    ".": "./src/index.tsx",
    "./themes": "./src/themes.mjs"
  }
}
```

The headless entry imports only `@angee/ui/theme-runtime`. It must not import
React, the UI root barrel, generated GraphQL or browser globals:

```js
import { defineTheme } from "@angee/ui/theme-runtime";

export const themes = [defineTheme({
  contractVersion: 1,
  id: "acme.paper",
  labelKey: "paper.label",
  descriptionKey: "paper.description",
  revision: 1,
  tokens: {
    shared: { "--r-6": "4px" },
    light: { "--surface-canvas": "#f4f0e6" },
    dark: { "--surface-canvas": "#211f1b" }
  }
})];
```

The browser entry imports the exact same object and attaches presentation:

```ts
import { defineBaseAddon } from "@angee/app";
import { defineThemeContribution } from "@angee/ui/theme";
import { themes } from "./themes.mjs";

export default defineBaseAddon({
  id: "theme.paper",
  themes: [defineThemeContribution({ definition: themes[0] })],
  i18n: {
    themes: {
      "paper.label": "Paper",
      "paper.description": "A quiet editorial design."
    }
  }
});
```

Theme IDs are globally unique stable preference keys. Labels may change.
Definitions use only the published semantic token allowlist; values are bounded
and cannot contain CSS rules, URLs or declarations. Put complex authored design
in a stylesheet listed by the definition. Every visual selector in that file
must be gated by its theme ID, and asset paths must be local and relative:

```js
stylesheets: ["./theme.css"]
```

```css
[data-theme-id="acme.paper"] .acme-paper-mark {
  background-image: url("./paper-mark.svg");
}
```

Options declare one current version, defaults, a strict parser and a resolver
that returns token layers. Inputs must be a closed, bounded set such as approved
enums, numeric ranges or six-digit hex colors. User-provided CSS, URLs, classes
and module paths are outside the contract. An optional React `optionsEditor`
edits an envelope and applies it through the shared Appearance action.

The complete consumer example is
[`examples/addons/example/theme`](../../examples/addons/example/theme). The
framework catalogue under `addons/angee/theme_*` shows fixed, asset-bearing and
configurable variants.

## Build outputs and migration

`angee build` discovers `./themes` only for composed addon packages and writes
`runtime/web/themes.css` plus `runtime/web/themes.catalog.json`. The project CSS
entry imports the generated stylesheet; Vite resolves theme-relative images and
fonts through its normal asset pipeline. Browser composition also checks that
each addon contribution references its canonical discovered definition.

Projects created before this feature need a template update. Re-render the
project template, review the explicit `/src/index.css` head link and virtual
`virtual:angee-appearance` wiring, then run the normal Angee build and production
web build. The deprecated color-scheme names continue to delegate during this
cycle, but new code should import `ColorSchemePreference` and
`useColorSchemePreference` and target `data-color-scheme`.

`angee.appearance_integrate` is optional. It adds a signed-in GraphQL analysis
operation and a tool on the Appearance page. The server fetches only bounded
public website facts through the shared pinned HTTP transport. Remote HTML,
styles and images are never inserted into the browser.
