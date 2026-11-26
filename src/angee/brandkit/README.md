# Brand kits

A deployment's brand facts in one place, for the themes, mail and documents that
must look like the organization. The [models module](models.py) and the
[fields module](fields.py) own the contracts below; their docstrings carry the
exact API.

## The kit

A `BrandKit` states one brand:

- `name`: the brand name, shown as a wordmark where no logo shows;
- ten colours, one per hex input of theme customisation, named exactly as the
  theme runtime names them (`brand`, `accent`, `neutral`, `canvas`, `surface`,
  `rail`, `success`, `warning`, `danger`, `info`);
- `font_heading` and `font_body`: CSS font stacks;
- `logo` and `logo_dark`: images for light and for dark backgrounds;
- `footer_organization` and `footer_address`: what a mail footer names;
- `voice`: how the brand writes, for people and for AI drafts.

A kit states light colour values, the seeds. A customisable theme takes them as
its options and derives its dark values itself, so a kit needs no translation
table to dress a theme. A fixed theme takes no options.

## Validation

Each colour is a `#rrggbb` `ColorField`. A font stack is written into CSS, where
HTML autoescaping protects nothing, so `FontStackField` admits only family names
of letters, digits, spaces and hyphens, each bare or wholly quoted, separated by
commas and closed by an unquoted CSS generic family such as `sans-serif`. An
unquoted family is a sequence of CSS identifiers, and one invalid word voids the
whole declaration, so no word of a bare family may start with a digit, be a lone
hyphen or start with a hyphen and a digit, or be a CSS-wide keyword (`inherit`,
`initial`, `unset`, `default`, `revert`, `revert-layer`); quote such a family
instead, as in `'Font Awesome 5 Free'`. Double quotes are stored as single quotes. The field checks every write that reaches the
database, not only a full clean: it checks a value of any type, and refuses an
expression (`F()`, `Value()`, a function, or `bulk_update`, which writes through
`Case`), whose result the database computes past the check. Its check takes time
linear in the value's length.

A logo must be an image storage's `FileManager.ingest_image` put on its image
drive, which every signed-in user reads; `BrandKit.clean` refuses any other file
when a logo is set or changed, so a logo trashed later never blocks an unrelated
edit.
No console root writes the logos yet: they wait for an image upload storage owns.

## The default kit

Once any kit exists, writes through `BrandKit.save` and `BrandKit.clean` (the
console and the resource loader) keep exactly one default:

- the first kit saved while no kit is the default becomes the default;
- saving another kit as the default takes the flag from the previous one in the
  same transaction (`BrandKitManager.demote_defaults`), and a conditional unique
  constraint refuses a second default written any other way;
- the default kit cannot be unticked (`BrandKit.clean`) or deleted
  (`BrandKit.delete_blocker`, enforced on every delete path, which locks the row
  first): make another kit the default first;
- changes of default made at once take turns: every kit is locked, in key order
  (`BrandKitManager.lock_default`), before any kit row by a save that can move
  the default, every console write and changeset revert of a kit
  (`BrandKit.lock_subject`), every delete (`BrandKit.lock_for_delete`) and a
  resource load that writes kits, once before its first row (`BrandKitResource`),
  so the second change waits and then takes the default from the first;
- a save that marks a stored kit not the default rereads its flag under the
  kit's row lock and refuses as `BrandKit.clean` does, so an edit cleaned before
  its kit became the default never leaves no default.

Two writers take kit rows without that lock. A file purge clears the logo of
every kit that shows the file, under the history capture's row lock, in scan
order: against a change of default it can deadlock, and one of the two then fails
with a database error. And an empty table has no row to lock: of two first kits
created at once, the second fails on the one-default constraint with a database
error, which a person does not reach because provisioning loads the first kit.

`BrandKit.objects.default()` returns the default kit the actor reads, or `None`:
before any kit exists, as in a database before its first resource load; after a
write that goes past `save` and `clean`, such as `bulk_create` into an empty table
or `update(is_default=False)`; and for an actor that reads no kit. A caller keeps
a stated behaviour for `None`. The console's `default_brand_kit` query returns it.

The install tier ships one kit stating the Angee brand. It states no default, so
on a fresh install it becomes the default by being the first kit saved. Its
colours are the Angee theme's customisation defaults, so applying it to that theme
changes nothing. The manifest declares every seeded field `create_only`: a reload
never overwrites a deployment's edit. A deployment may delete the shipped kit once
another kit is the default; the next load recreates it, as it does every seeded
row, and it comes back as an ordinary kit, because a default already exists.

## Mail colours

Mail works its colours out from the seeds; the kit stores no dark value and no
mail colour. `BrandKit.mail_palette()` returns plain values, keyed `light` and
`dark`, then by role: `canvas`, `surface`, `text`, `muted`, `border`, `primary`,
`on_primary` and `link`, each a lower-case `#rrggbb`; a seed stored past
validation raises instead of coming back as written. The light canvas, surface
and primary are the kit's seeds as stated; every other role follows the theme
runtime's derivation for a customised theme (text, muted and border from the
neutral, each dark value from its seed). `on_primary` is the theme ink that reads
on primary, else black.

Each scheme guarantees the WCAG AA minimum of 4.5:1 for text, muted and link on
canvas and on surface, and for `on_primary` on primary. A light canvas or surface
too dark for the dark ink is lightened first; then a text colour that would read
less moves the least toward the scheme's ink that reads.

## Contrast

`BrandKit.contrast_warnings` names the roles the palette had to move far from their
seeds: a stated seed (the light canvas and surface) that moved at all, and a
derived role that moved more than half way to its ink, such as a brand too light
to colour a link. The palette repairs every pair, so a warning names a seed mail
cannot wear as stated. It covers mail only, not the app a theme dresses. It is
advice: the kit form shows each as a warning, and a save never refuses one.

## Access

Every signed-in actor reads kits; platform admins create, edit and delete them
([permissions](permissions.zed): `brandkit/brand_kit`).

## Console

Settings holds the Brand kits page for those who may create kits: the list of
kits and the registered kit form. The form groups the colours as the theme
customisation editor does and shows a saved kit's contrast warnings, each
focusing the colour it names. It does not edit the logos.
