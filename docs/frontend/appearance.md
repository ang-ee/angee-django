# Appearance and theme addons

Angee treats a visual design as an installed addon. A theme addon contributes
one or more named implementations; Appearance is the runtime setting that
selects one of those implementations, its light/dark behavior and any bounded
options it declares. Templates remain scaffolds or content structures.

The renderer has an intrinsic base token set even when no theme addon is
installed. `angee.theme_stock` makes that unchanged blue look selectable as
**Default** under the stable `angee.stock` preference key. Installing another
theme adds it to the catalogue and never activates it by import order.

The framework identity catalogue includes three product themes. **Angee**
(`angee.theme_angee`, theme ID `angee.angee`) uses the `angee.ai` graphite and
gold identity; **Fyltr** (`angee.theme_fyltr`, theme ID `angee.fyltr`) uses the
`fyltr.ai` charcoal, cool-white and green identity; **Ledger**
(`angee.theme_ledger`, theme ID `angee.ledger`) uses ink on warm paper with a
solar accent and monochrome charts. Each is one selectable theme with light and
dark token layers. Default, Angee, Fyltr and Ledger all accept the shared bounded
customization described below. The full project profile installs all three
product themes; the default profile remains Default plus Appearance.

## Host and user precedence

Project and stack templates expose three build inputs:

- `appearance_theme_id`, which defaults to `angee.stock`;
- `appearance_color_scheme`, one of `system`, `light` or `dark`;
- `appearance_options`, an optional JSON `{ "version": ..., "value": ... }`
  envelope matching the selected theme's current options version.

The Vite configuration validates those values against the generated catalogue.
Changing a host default therefore requires the normal codegen and web build.
The synchronous head bootstrap applies that default, or a validated actor cache,
before the application entry loads. Vite bundles the React-free
[`bootstrapAppearance`](../../packages/ui/src/theme/runtime.mjs) owner; bootstrap
and React share its token validation, cache protocol and root application.

One installed addon may supply the host theme's options at run time, through the
install-one `hostAppearance` manifest key
([`HostAppearanceSource`](../../packages/app/src/define-addon.ts)). `createApp` asks
it about the host theme: the selected app's, else the build's, locked or not. A
theme whose options decline the source (`acceptsHostAppearanceSource: false`, as
Fyltr and Ledger state) is not put to it: the source is asked about no theme and no
build options, and `createApp` wears none of its answer, as it wears none for a
theme without options, so the source never holds the first paint or the boot cache
for either, and a declining theme wears the build's options when it is the build's
own theme, else its own defaults. The
options it answers replace the build's options for that theme; when it answers
none, the build's stand. The [brand kit](../../src/angee/brandkit/README.md#the-apps-colours)
is that source. While the source's first read is on its way, the provider keeps
each person's first paint, and the boot cache, where they wear the host's
options: a person following the app default, or a locked app. A person's own saved
theme or customization is painted at once. After the first paint, every choice
applies at once, a draft's included; a return to the app default shows the build's
options until the source answers. A failure or a pause releases the hold onto the
build's options. Pages render meanwhile under that paint: on a device's first
visit it is the build's appearance, which changes once when the answer arrives;
on later visits it is the cached answer, so nothing changes. Every page inside the app wears the answer where it draws from
tokens, signed in or out; a standalone web form page (`/forms/*`) keeps the build's
look.

A signed-in user's `iam.User.preferences.appearance` fields override the host,
run-time options included, independently. The stored document contains stable
IDs and versioned inputs:

```json
{
  "version": 1,
  "themeId": "angee.stock",
  "colorScheme": "system",
  "options": {
    "version": 3,
    "value": {
      "brand": "#5b5bd6",
      "accent": "#0d9488",
      "neutral": "#6b7280",
      "canvas": "#f7f8fa",
      "surface": "#ffffff",
      "rail": "#0a0c10",
      "success": "#10b981",
      "warning": "#f59e0b",
      "danger": "#ef4444",
      "info": "#3b82f6",
      "font": "theme",
      "radius": "soft",
      "density": "theme",
      "elevation": "theme",
      "logo": "brand"
    }
  }
}
```

A selected app may lock its theme (`themeLocked: true` beside the app's or its
root's `theme`; see [`selectApp`](../../packages/app/src/boot-app.tsx)). Only the app
selection locks: the build defaults cannot. Within that app the locked theme and the
host options replace the person's `themeId` and `options`, which stay saved and apply
again in unlocked apps; their `colorScheme` still applies. The device's boot cache
keeps the person's own choice there, resolved as if the app were not locked, so a
scheme chosen in a locked app paints on the next load. The Appearance page shows
why, disables the theme choices (describing them by that reason), and hides the
customization and the theme tools there.

An absent field follows the corresponding host default. Selecting a different
theme replaces the old theme's options. Reset removes the complete appearance
slice. If a saved theme is later uninstalled, the UI uses the host or intrinsic
fallback and retains the unavailable ID so reinstalling the addon can restore
the selection.

Theme IDs are exact canonical IDs and options envelopes must match the selected
implementation's current version. Compatibility aliases and predecessor option
migrators are not part of the current renderer.

Before crossing this cutoff, save older selections and options through Appearance
on the pre-refactor snapshot. Browser-only preferences are separate from
the server database: a device may still hold `angee:theme` or a `pendingLegacy`
entry in `angee:appearance`. While signed in on the snapshot, open Appearance and
save the intended scheme and theme, or manually transfer that choice to the current
Appearance page. The new code neither consumes those old keys nor deletes them; its disposable
first-paint cache uses the separate current `angee:appearance:3` protocol.
A database history reset does not prove every browser has completed this step;
keep the snapshot available until each affected device's choice is preserved.

The DOM uses `data-theme-id` for implementation identity and
`data-color-scheme="light|dark"` for the resolved scheme. Theme identity and
color scheme have separate attributes.

The editable Appearance page keeps a provider draft open for its mounted
lifetime. Theme, color scheme, customization and contributed tools read and
edit `currentPreferences`, immediately re-theming the whole app and both preview
documents. Nothing persists until **Save** in the standard control band; Save
and **Discard** appear with the shared dirty tint while the draft differs from
the saved preference. Both leave the page ready for the next edit. Leaving with
unsaved changes uses the standard navigation guard; confirmed departure discards
the draft, and unmounting always restores the saved appearance. Read-only
appearance opens no draft and offers neither action. The boot cache contains
only saved values and both resolved scheme layers, so reloading restores the
saved appearance even when the device's color scheme changed between visits.

The three recovery verbs have distinct scopes: **Discard** restores the last
saved appearance; **Restore theme defaults** edits the draft inside the
customization editor; **Reset appearance** directly deletes the saved preference
and closes the draft, including when an unsupported preference prevents editing.
The user-menu scheme toggle edits the same draft while the page is open and
saves immediately when no draft is open. See the
[appearance provider](../../packages/ui/src/theme/appearance.tsx) for the command
contract.

## Customizing a base theme

Selecting a theme chooses the authored base design. A customizable theme then
accepts bounded inputs for identity, page and panel surfaces, app-rail chrome,
the four semantic status roles, typography, corners, control density, elevation
and logo treatment. The generator derives semantic tokens for light and dark
together, including readable foregrounds, borders, interaction states and the
complete solid/soft/surface status matrix. Appearance stores the inputs in the
user's preference and rebuilds the token overrides whenever the generator
changes.

Logo choices are named treatments of the shared `@angee/logo-react` mark:
theme default, brand palette, accent palette, monochrome, star and corner. The
app chooser, public brand mark and sign-in identity all read the same resolved
appearance choice. A theme preference therefore stores a small stable name
rather than image data or a remote asset URL.

An unchanged field emits no override. This keeps the selected addon's authored
tokens authoritative and makes **Restore theme defaults** exact. The editor
starts from the options the person wears: their own, else the app default's.
Customizing while following the app default therefore keeps the default's other
fields and pins the current base theme in the user's preference so a later
host-default change cannot reinterpret those options.

Theme authors opt into the shared customization contract from the pure headless
entry and attach the matching shared editor in the browser contribution:

```js
import {
  createThemeCustomizationOptions,
  defineTheme,
} from "@angee/ui/theme-runtime";

export const themes = [defineTheme({
  // identity and token layers omitted
  options: createThemeCustomizationOptions({
    brand: "#315c52",
    accent: "#8b5cf6",
    neutral: "#777064",
    canvas: "#f8f6f1",
    surface: "#ffffff",
    rail: "#18211e",
    success: "#16845b",
    warning: "#b7791f",
    danger: "#c2413a",
    info: "#3178c6",
    font: "theme",
    radius: "theme",
    density: "theme",
    elevation: "theme",
    logo: "theme",
  }),
})];
```

```tsx
import {
  defineThemeContribution,
  ThemeCustomizationEditor,
} from "@angee/ui/theme";

defineThemeContribution({
  definition: themes[0],
  optionsEditor: ThemeCustomizationEditor,
});
```

A customizable theme takes the answer of the run-time host appearance source
([host and user precedence](#host-and-user-precedence)) unless it declines it, as a
theme whose palette is its identity does:
`createThemeCustomizationOptions(defaults, { version, acceptsHostAppearanceSource: false })`.
The flag belongs to the options definition, not to the options envelope, so it
changes no options version and no saved customization; the build's stated
options and a person's own still apply.

## Authoring a theme addon

A model-less theme addon has the same `pyproject.toml` and web package structure as
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
and cannot contain CSS rules, URLs or declarations.
The allowlist includes three font families: `--font-family-sans` for body text,
`--font-family-mono` for code, and `--font-family-display` for the title-like text
roles (`display`, `title`, `heading`) and date tiles. An unset display family follows
the sans family, so a theme that names only a body face looks the same everywhere.
It also includes typography weights (integer 100–900), selected font sizes
(integer 10–40px) and line heights (integer 12–56px), chart series and surface
colours, and motion durations (integer 0–1000ms). Easing is limited to the
standard CSS keywords or `cubic-bezier(...)` with four finite numbers; chart
colours retain the general safe token-value bounds above.
The type scale is authored in pixels by design, and the theme validator enforces
those bounds.

Put complex authored design in a stylesheet listed by the definition. Every
visual selector in that file must be gated by its theme ID, and asset paths must
be local and relative:

```js
stylesheets: ["./theme.css"]
```

```css
[data-theme-id="acme.paper"] .acme-paper-mark {
  background-image: url("./paper-mark.svg");
}
```

The page icon and link-preview image are not theme assets: an addon names them in
`web/package.json` `angee.assets` and the deployment selects them through
[`ANGEE_UI.shell.site`](guidelines.md#rules), whatever theme is selected.

Options declare one current version, defaults, a strict parser and a resolver
that returns token layers. Inputs must be a closed, bounded set such as approved
enums, numeric ranges or six-digit hex colors. User-provided CSS, URLs, classes
and module paths are outside the contract. An optional React `optionsEditor`
edits an envelope and applies it through the shared Appearance action. Use
`createThemeCustomizationOptions` with `ThemeCustomizationEditor` for the
standard palette/type/shape controls; define theme-specific options only when
their inputs have different meaning.

The complete consumer example is
[`examples/src/example/theme`](../../examples/src/example/theme). The
framework catalogue under `src/angee/theme_*` shows fixed, asset-bearing and
configurable variants.

## Build outputs and migration

The explicit host build discovers `./themes` only for composed addon packages and writes
`runtime/web/themes.css` plus `runtime/web/themes.catalog.json`. The project CSS
entry imports the generated stylesheet; Vite resolves theme-relative images and
fonts through its normal asset pipeline. Browser composition also checks that
each addon contribution references its canonical discovered definition.

Projects created before this feature need a template update. Re-render the
project template, review the explicit `/src/index.css` head link and virtual
`virtual:angee-appearance` wiring, then run the runtime build and production
web build. Read and set a person's color scheme through `useAppearance`
(`useOptionalAppearance` where a provider may be absent) and target
`data-color-scheme`; `useColorSchemePreference` is the browser-local scheme of a
tree with no appearance provider, and mounting it under one writes over the saved
scheme.

`angee.appearance_integrate` is optional. It adds a signed-in GraphQL analysis
operation and a tool on the Appearance page, as an `appearance.settings#tools`
child. The server fetches only bounded
public website facts through the shared pinned HTTP transport. Remote HTML,
styles and images are never inserted into the browser.
