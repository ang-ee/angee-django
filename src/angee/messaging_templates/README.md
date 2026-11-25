# Message templates

The library outgoing mail is written from: shipped defaults that addons install,
the deployment's own text over them, the rule that picks a recipient's row, and the
renderer that turns a row and its values into a subject, a preheader, an HTML body
and a plain-text body. The [models module](models.py), the
[rendering module](rendering.py), the [engine](engine.py) and the
[walker](walker.py) own the contracts below; their docstrings carry the exact API.
This addon sends nothing: a sender renders a row and hands the result on.

## The rows

A `MessageTemplate` is one row of one kind for one key and one language. The
kind is written once and decides what the row is (`MessageTemplateKind.handler`):

- a message template of the kind **template** is a mail: a `subject`, a
  `preheader` and the `layout` it sits in, whose key must resolve (below). A
  shipped template's key is its addon's label and a name
  (`iam_messaging.sign_in_link`) by convention; a deployment's own may be bare;
- a message template **layout** wraps templates; it is HTML;
- a message template **block** is a styled piece of a mail, and a message
  template **part** a piece of wording, that rows include.

A layout, block or part has no subject, preheader or layout. Every row has a
`format`, Markdown or HTML (`MessageTemplateFormat`), and a `source`; an HTML row
also has its plain-text form in `text_source`, a Markdown row has none.
`language` is an ISO 639 code spelled as parties stores a person's languages
(`LanguageCodeField`): `en`, `zh`, `yue`, never `en-us`. Database constraints
hold the kind-only columns, the layout's HTML, the text form and the flags;
`MessageTemplate.clean` refuses the rest.

Rows are written one at a time: `save()` derives the content hash and guards the
write-once kind, key, language and flag, so the querysets refuse `update()`,
`bulk_update()` and `bulk_create()`.

## Variables

`variables` declares the values a row reads, as dotted paths of lower-case names
to a description (`{"order.total": "The total, formatted"}`); a later segment may
be `*`, every item of a list (`tags.*`). A block's or part's `variables` are its
arguments. A row declares at most 50 paths, each joining at most 8 names. No path
is a proper prefix of another, none reads one value both as a list and as an
object (`items.*` beside `items.count`), and none starts with a name the mail
around a row supplies (`brand`, `lang`, `dir`, `subject`, `preheader`) or Django's
template engine uses (`forloop`, `block`). `validate_variables` in the rendering
module is the one rule; a caller judging several rows' declarations together
passes its own limit on their paths. `sample` holds values to preview the row
with.

## Writing a row

A row is Django template code with a restricted library, the tags and filters
the [engine](engine.py) allows (`ALLOWED_TAGS`, `ALLOWED_FILTERS`): a row writes
`if`, `for`, `with`, `comment`, `include` and `templatetag`
(`{% templatetag openvariable %}` writes a literal `{{`), a layout writes `block`
for its content, and a value goes through Django's own filters and `url`.
Anything else fails to compile.

- A template names its layout in its `layout` column and never writes
  `{% extends %}` or `{% block %}`; a layout holds one empty top-level
  `{% block content %}{% endblock %}` in each form, where the template goes.
- Blocks and parts are included by name, every value passed by name, always
  `only`: `{% include "blocks/button" with label="Sign in" url=link only %}`.
  An included row reads only its arguments and `brand`, `lang` and `dir`.
- Every value a row reads is declared in its `variables`, `*` for list items.
  `brand` holds the kit's name, fonts, footer and mail colours
  (`brand.light.primary`), `lang` and `dir` the row's language and its writing
  direction, and a template or layout reads `subject` and `preheader`.
- A link a value supplies goes through `url` (`href="{{ link|url }}"`), which
  gives an empty link for anything but a safe URL; dates go through `date`
  (`date:"DATE_FORMAT"`), money through `floatformat`. A path printed with a
  time (`DATETIME_FORMAT`, `H:i`) takes an aware `datetime`, never a bare date.
- A start tag holding template code is written plainly: lower-case names, each
  attribute once, each value double-quoted, with `&amp;`, `&lt;`, `&gt;` and
  `&quot;` for those characters. Every row's HTML ends in text, outside any tag,
  comment or `<style>`, since the row it is included in continues it.
- A Markdown row is rendered by markdown-it first, with raw HTML off: template
  code stays whole, `[label]({{ link }})` is a link through `url`, a paragraph
  holding only tags writes no `<p>`, and an `include` stands alone in its
  paragraph. Its plain-text form is written from the same Markdown.

The walker (`TemplateWalker`) refuses what would not render safely when a row is
saved (`clean()`, when what it renders changed), after each resource load, and
again before every render: oversized sources and lines, literals holding `<`,
`>` or `"`, costly filter use or filter arguments Django's filters fail on,
template code the Markdown pass would not keep whole, HTML a mail may not carry
(scripts, raw-text elements such as `noscript` and `textarea`, event
attributes, comments but a layout's Outlook block, a link that is not one `url`
output or a safe static URL, a static value reaching a scheme `url` refuses,
styles reading anything but the brand's colours and fonts), reads no row
declares (`subject` and `preheader` are read in the HTML form alone), include
cycles, and the walk's bounds on nodes, outputs, loops, includes, text and the
rows it reaches.

## Rendering

`row.render(values, kit=None)` walks the row, shapes `values` to what it
declares (`TemplateWalk.prepare`: undeclared keys dropped, every required value
present and not null, every declared path filled, ISO dates parsed at the paths
read through `date`), and renders it on Django's template engine in the row's
language: the subject and preheader on one line each, the HTML body escaping
every value, and the plain-text body from the rows' text forms. A render reads
rows and the brand kit through the caller's scope, the default kit when `kit` is
`None`. It raises `ValidationError` and nothing else: when the row, the values
(a number no float holds, a moment the local zone cannot hold, a bare date
printed with a time), a row it needs, the kit or a pass's work budget
(`RenderBudget`; the subject and preheader get a tenth of a body's) refuses, and
with the code `render_failed` for any other failure in a pass. The subject and
preheader drop control and bidirectional override characters. A sender picks the
row with `effective("template", key, person=recipient)` and renders it as the
system actor.

`row.preview(kit=None)` renders the row's `sample`: a template whole, a layout
around a fixed body, subject and preheader, a block or part alone. It returns
the rendered message, or none, and warnings (`TemplateWarningCode`): a long
subject, an empty or long preheader, a declared value nothing reads, a declared
`unsubscribe_url` the body does not show, an empty plain-text body, HTML near
Gmail's clipping size, or the render error. The console's `message_template_preview`
read returns it to administrators (`preview` permission), in the kit it names or
the default one.

## Shipped defaults, overrides and reset

An addon ships rows in its `install` resources, loaded through
`MessageTemplateResource` (resources' `BaselineResource`): each is a
**baseline**, readable by every signed-in person and written only by loads; a
changed row in a later release re-applies to it. A row's `content_hash`
fingerprints its kind and what it renders. A load saves rows in file order and a
save walks what the row includes, so a template's layout and every block or part
a row includes load before it (`depends_on`).

A baseline is walked among the shipped rows alone, when it saves and again after
every load, so a shipped defect fails the load. After a load each baseline is
also judged among the rows that render: a refusal reached only through the
deployment's own row or override, one the shipped row includes or is laid out
by, does not fail the load. It is logged as a warning naming the shipped row,
the deployment's row and the refusal, and the shipped row refuses at render
until the deployment fixes its row. Saving an included row does not judge the
rows that include it.

This addon ships the `base` layout, the `header`, `footer`, `button`, `quote` and
`record_card` blocks and the `greeting`, `ignore_notice` and `signature` parts, in
English and, for the parts, Spanish. The layout is one 600px column with the
header and footer blocks around the content; it writes the light colours inline
and sets the dark ones in `<style>` from role classes (`brand-text`,
`brand-primary`, …), and `.brand-radius` rounds the button and the record card.
Classic Outlook reads its conditional comments: the DPI setting, a font override
and a ghost table 600 wide. Only a layout may carry Outlook's comments, static
and balanced, their markup checked as any other.

Another addon ships a template as an `install` resource file targeting
`messaging_templates.MessageTemplate`, declared after this addon's layout:

```toml
[tool.angee.resources]
install = [
  { path = "install/010_<addon>.messagetemplate.yaml", depends_on = "messaging_templates:install/030_messaging_templates.messagetemplate.yaml" },
]
```

Each row states `kind: template`, a key `<addon label>.<name>`, `language`,
`name`, `format`, `subject`, `preheader`, `layout: base`, `source` (and
`text_source` for HTML), `variables` and a `sample`; blocks and parts are
included with `{% include "…" with … only %}`, never `{% extends %}`.

- `MessageTemplate.objects.override(baseline)` starts the deployment's
  **override**: the non-baseline row with the baseline's kind, key and language,
  copied from it (`MessageTemplate.override_values`). Calling it again returns
  the same row.
- An override records its baseline's fingerprint when it starts. When a release
  changes the baseline, the override **has an updated default** until
  `MessageTemplate.objects.keep_override(override)` keeps its text over the new
  default, or the override is deleted.
- Deleting an override **resets** to the default. Reverting that delete brings
  the override back with the fingerprint it held.

A non-baseline row with no baseline is the deployment's own. Two consequences
follow from that rule:

- a language that gains a baseline later moves its recipients off an override in
  another language they read before, and nothing flags the move;
- a baseline that arrives over a deployment's own row makes it an override that
  has an updated default, and resetting it then deletes the deployment's text.

## The row a recipient reads

`MessageTemplate.objects.effective_rows(kinds, keys=..., language=..., person=...)`
resolves many keys in one query; `effective(kind, key, ...)` resolves one, and
raises when none is found. Per key, the languages tried are the one asked for, or
else the person's languages, then the default language (`LANGUAGE_CODE`'s primary
subtag); the first with a row wins, its override before its baseline. When none
has one, the language of a baseline wins, English first and then by code.
Callers pass languages as parties stores them, a primary subtag (`en`, `zh`), not
a locale such as `en-us`. Both read through the caller's scope; work with no actor
reads through `MessageTemplate.system_queryset()`.

A key **resolves** (`resolves(kind, key)`) when it has a baseline in some language
or a row in the default language, so every recipient finds it. A template's
layout must resolve, judged when the layout is set or changed, through the
reader's own scope, and so must every block or part a saved row includes. A row
cannot be deleted while other rows use its key (a template its layout, any row a
block or part it includes) and the key would stop resolving without it. Each row
is judged with the others still there: one queryset delete from code that
removes every row keeping a key resolvable, a baseline among them, passes, and
the console, which deletes one row by its id, cannot reach it. A row naming a key
can race that key's delete; the render judges the result. A baseline a later
release no longer ships stays, and stays effective.

Each row reports `is_effective` (what renders for its kind, key and language),
`is_overridden` (a baseline with an override) and `has_updated_default`, as fields
and as console filters.

## Access

Every signed-in actor reads message templates; platform admins create, edit and
delete the deployment's own rows and overrides, read every sample and preview
every row. Nobody but a resource load writes a baseline
([permissions](permissions.zed): `messaging_templates/message_template`).

## Console

The `message_templates` resource lists, reads, inserts, edits and deletes rows;
`override_message_template` and `keep_message_template_override` take a row's id
and return an `ActionResult` naming the override. They refuse as the framework's
actions do: a signed-out caller gets the session's GraphQL error, and a caller
who may not create or write the override a `PERMISSION_DENIED` GraphQL error; an
unreadable or missing row, a row that is not a shipped default (`not_a_baseline`)
and an override of none (`no_baseline`) come back in band as `ok: false`.

`message_template_preview(template, brand_kit)` returns a row's preview, in the
named kit or the default one, to a caller who may preview it. It refuses as the
verbs do: a signed-out caller gets the session's GraphQL error, and a caller who
reads the row but may not preview it a `PERMISSION_DENIED` GraphQL error.
