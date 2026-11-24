# Brand kits

A deployment's brand facts in one place, for the themes, mail and documents that
must look like the organization. The [models module](models.py) owns the
contracts below; its docstrings carry the exact API.

## The kit

A `BrandKit` states one brand:

- `name`: the brand name, shown as a wordmark where no logo shows;
- ten colours, one per hex input of theme customisation, named exactly as the
  theme runtime names them (`brand`, `accent`, `neutral`, `canvas`, `surface`,
  `rail`, `success`, `warning`, `danger`, `info`);
- `font_heading` and `font_body`: CSS font stacks;
- `logo` and `logo_dark`: files for light and for dark backgrounds;
- `footer_organization` and `footer_address`: what a mail footer names;
- `voice`: how the brand writes, for people and for AI drafts.

A kit states light colour values, the seeds. A customisable theme takes them as
its options and derives its dark values itself, so a kit needs no translation
table to dress a theme. A fixed theme takes no options.

## Validation

Each colour is a `#rrggbb` `ColorField`. A font stack is written into CSS, where
HTML autoescaping protects nothing, so `validate_font_stack` admits only family
names of letters, digits, spaces and hyphens, each bare or wholly quoted,
separated by commas and closed by an unquoted CSS generic family such as
`sans-serif`.

## The default kit

At most one kit is the default. Saving a kit as the default clears the previous
default in the same transaction (`BrandKitManager.demote_defaults`), and a
conditional unique constraint refuses a second default written any other way.
`BrandKit.objects.default()` returns the default kit the actor reads, and the
console's `default_brand_kit` query returns it.

The install tier ships one kit stating the Angee brand, the default on a new
deployment. Its colours are the Angee theme's customisation defaults, so applying
it to that theme changes nothing. The manifest declares every seeded field
`create_only`: a reload never overwrites a deployment's edit, and never takes the
default back from a kit the deployment chose.

## Mail colours

Mail works its colours out from the seeds; the kit stores no dark value and no
mail colour. `BrandKit.mail_palette()` returns plain values, keyed `light` and
`dark`, then by role: `canvas`, `surface`, `text`, `muted`, `border`, `primary`,
`on_primary` and `link`. The light scheme shows the canvas, surface, neutral (as
muted text) and brand seeds as stated; text and border come from the neutral, the
dark scheme's values from each seed, as the theme runtime derives the app's
tokens. `on_primary` is the theme ink that reads on primary, else black.

Each scheme guarantees the WCAG AA minimum of 4.5:1 for text, muted and link on
canvas and on surface, and for `on_primary` on primary. A light canvas or surface
too dark for the dark ink is lightened first; then a text colour that would read
less moves the least toward the scheme's ink that reads.

## Contrast

`BrandKit.contrast_warnings` names the roles the palette had to move far from their
seeds: a stated seed (the light canvas, surface and muted text) that moved at all,
and a derived role that moved more than half way to its ink, such as a brand too
light to colour a link. The palette repairs every pair, so a warning names a seed
mail cannot wear as stated. It is advice: the kit form shows each as a warning, and
a save never refuses one.

## Access

Every signed-in actor reads kits; platform admins create, edit and delete them
([permissions](permissions.zed): `brandkit/brand_kit`).

## Console

Settings holds the Brand kits page: the list of kits and the registered kit form,
which groups the colours as the theme customisation editor does and shows a saved
kit's contrast warnings, each titled with the seed's label.
