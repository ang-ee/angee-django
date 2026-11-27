---
"@angee/ui": minor
"@angee/app": minor
---

The keyboard focus ring is Tailwind's ring layer. `focus-ring` and
`focus-ring-danger` write Tailwind's ring variable instead of a `box-shadow`
of their own, so the one `box-shadow` Tailwind composes carries the ring and
the element's own `shadow-*` elevation side by side.

What a keyboard user sees:

- In forced-colours mode (Windows High Contrast), every control that draws the
  ring shows a 2px outline in the system's colour, 2px outside it. The browser
  drops every `box-shadow` in that mode; in the last release no control that
  draws the ring showed focus there (controls with their own outline or border,
  such as the calendar's buttons, did). The outline is declared for forced
  colours only: normal mode paints no outline.
- A focused control keeps its elevation shadow beside the ring: the active pill
  tab, the pressed segmented toggle, the drawer rail's tabs, a toast, the
  block-card link, an interactive card and a board or gallery card's frame.
- An open tab panel, which Base UI puts in the tab order, shows the ring when
  the keyboard focuses it.
- In the form's editable fields, a focused input, textarea or button directly
  inside the field drops its own ring for the field's; a control that draws a
  focus-within ring on an element of its own (number, search, JSON, markdown,
  tags) keeps it.

The ring's colour, width and offset are the theme's `--ring` and
`--ring-danger`, unchanged in name and value, whatever elevation a theme or a
person chooses.

An elevation token is a shadow, never `none`. A `shadow-*` utility composes
`--elevation-*` into the same `box-shadow` as the ring, and a layer a browser
cannot draw voids the whole list, ring included. "No elevation" is written
`0 0 #0000` (the Ledger theme and the flat elevation choice now do). The theme
contract holds every elevation, `--ring` and `--ring-danger` to a list of
shadows in fixed forms, wherever a token layer is checked (a theme definition,
its resolved options and a document's appearance): per layer, an optional
`inset`, two to four lengths in `0`, `px`, `rem` or `em`, and an optional hex,
`transparent`, `currentcolor`, `rgb()`/`rgba()`, `rgb(from … r g b / a)` or
`color-mix(in …, … %, transparent)` colour. Anything else is refused, `none`,
`0`, a named colour, `calc()` and other units included. `isShadowList` exports
that judge and `THEME_ELEVATION_PRESETS` the elevation choices. A theme of
yours that writes another value changes it to one of the forms.

`@angee/app`: the composer refuses a theme stylesheet that declares an
`--elevation-*` property, names one in `@property`, or holds a backslash, reading the text with its comments and strings, and names the
theme; a theme sets its elevations in its definition, where the contract checks
them. Other custom properties a theme stylesheet declares are untouched. An
elevation set outside a theme (an addon's own stylesheet, the host's CSS, an
inline style) is not checked. `assertThemeStylesheet`
(`@angee/app/web-codegen`) is that check.

Code of yours that removes a ring writes `ring-0`, and `outline-none` for
forced colours, under a selector that outranks the ring's state variant:
`shadow-none` clears an element's own shadow, never the ring, which is a layer
of its own. An elevation written as a raw `box-shadow` (an arbitrary
`[box-shadow:…]`, an inline style) competes with the ring: spell it with a
`shadow-*` utility to keep both.

The outline needs the `forced-colors` media query: a browser without
`forced-colors` support shows no indicator in that mode.
