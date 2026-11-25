# Message templates

The library outgoing mail is written from: shipped defaults that addons install,
the deployment's own text over them, and the rule that picks a recipient's row.
The [models module](models.py) and the [rendering module](rendering.py) own the
contracts below; their docstrings carry the exact API. This addon renders nothing
yet and ships no rows: the renderer, and the rows it checks, build on it.

## The rows

A `MessageTemplate` is one row of one kind for one key and one language. The
kind is written once and decides what the row is (`MessageTemplateKind.handler`):

- a **template** is a mail: a `subject`, a `preheader` and the `layout` it sits
  in, whose key must resolve (below); its key is its addon's label and a name
  (`iam_messaging.sign_in_link`);
- a **layout** wraps templates; it is HTML;
- a **block** is a styled piece of a mail, and a **part** a piece of wording,
  that rows include.

A layout, block or part has a bare key and no subject, preheader or layout. Every
row has a `format`, Markdown or HTML (`MessageTemplateFormat`), and a `source`; an
HTML row also has its plain-text form in `text_source`, a Markdown row has none.
`language` is an ISO 639 code spelled as parties stores a person's languages, and
one Django knows. Database constraints hold the kind-only columns, the layout's
HTML, the text form and the flags; `MessageTemplate.clean` refuses the rest.

## Variables

`variables` declares the values a row reads, as dotted paths of lower-case names
to a description (`{"order.total": "The total, formatted"}`); a later segment may
be `*`, every item of a list (`tags.*`). A block's or part's `variables` are its
arguments. A row declares at most 50 paths, each joining at most 8 names. No path
is a proper prefix of another, none reads one value both as a list and as an
object (`items.*` beside `items.count`), and none starts with a name the renderer
supplies (`brand`, `lang`, `dir`, `subject`, `preheader`, `forloop`, `block`).
`validate_variables` in the rendering module is the one rule; the renderer runs it
too, with its own limit on the paths of a template and its layout together. `sample` holds values to preview the row with.

## Shipped defaults, overrides and reset

An addon ships rows in its `install` resources, loaded through
`MessageTemplateResource`: each is a **baseline**, readable by every signed-in
person and written only by loads; a changed row in a later release re-applies to
it. A row's `content_hash` fingerprints what it renders.

- `MessageTemplate.objects.override(baseline)` starts the deployment's
  **override**: the non-baseline row with the baseline's kind, key and language,
  copied from it (`MessageTemplate.override_values`). Calling it again returns
  the same row.
- An override records its baseline's fingerprint when it starts. When a release
  changes the baseline, the override shows **default updated** until
  `MessageTemplate.objects.keep_override(override)` keeps its text over the new
  default, or the override is deleted.
- Deleting an override **resets** to the default; its history ends there.

A non-baseline row with no baseline is the deployment's own. Two consequences
follow from that rule:

- a language that gains a baseline later moves its recipients off an override in
  another language they read before;
- a baseline that arrives over a deployment's own row makes it an override that
  shows default updated, and resetting it then deletes the deployment's text.

## The row a recipient reads

`MessageTemplate.objects.effective_rows(kinds, keys=..., language=..., person=...)`
resolves many keys in one query; `effective(kind, key, ...)` resolves one, and
raises when none is found. Per key, the languages tried are the one asked for, or
else the person's languages, then the default language (`LANGUAGE_CODE`'s primary
subtag); the first with a row wins, its override before its baseline. When none
has one, the language of a baseline wins, English first and then by code.

A key **resolves** (`resolves(kind, key)`) when it has a baseline in some language
or a row in the default language, so every recipient finds it. A template's
layout must resolve. A row cannot be deleted while other rows use its key and the
key would stop resolving without it; a template naming a key can still race that
key's delete, and the renderer judges the result. A baseline a later release no
longer ships stays, and stays effective.

Each row reports `is_effective` (what renders for its kind, key and language),
`is_overridden` (a baseline with an override) and `default_updated`, as fields and
as console filters.

## Access

Every signed-in actor reads message templates; platform admins create, edit and
delete the deployment's own rows and overrides and read samples. Nobody but a
resource load writes a baseline ([permissions](permissions.zed):
`messaging_templates/message_template`).

## Console

The `message_templates` resource lists, reads, inserts, edits and deletes rows;
`override_message_template` and `keep_message_template_override` take a row's id
and return an `ActionResult` naming the override.
