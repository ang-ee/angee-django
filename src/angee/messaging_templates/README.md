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
  gives an absolute `https`, `http` (to a named host, with no userinfo),
  `mailto` or `tel` link, percent-encoded, or none; dates go through `date` (`date:"DATE_FORMAT"`), money through
  `floatformat`. A path printed with a time (`DATETIME_FORMAT`, `H:i`) takes an
  aware `datetime`, never a bare date. `linebreaksbr` writes markup, so it is
  the last filter of a printed value.
- HTML is written in one strict spelling that every reader tokenises alike
  (`SpellingRule` in the [HTML checks](html_checks.py) states each rule with
  its refusal): no control character but tab and newline; a `<` only to open a
  tag, an end tag, or in a layout its opening `<!doctype html>` and Outlook's
  comments, `&lt;` anywhere else; each attribute once, every value
  double-quoted with `&amp;`, `&lt;`, `&gt;` and `&quot;` for those characters
  (the case of names and the white space between attributes are free); `/>`
  only on a void element; an end tag written `</name>`; template code only in
  text or a double-quoted value; only the elements a mail is written with; and
  every form ending in text, outside any tag, comment or `<style>`, since the
  row it is included in continues it. A note is written `{# … #}`.
- A Markdown row is rendered by markdown-it first, with raw HTML off: template
  code stays whole, `[label]({{ link }})` and `[{{ label }}]({{ link }})` are
  links through `url`, a paragraph holding only tags writes no `<p>`, and an
  `include` stands alone in its paragraph. Its plain-text form is written from
  the same Markdown.

The walker (`TemplateWalker`) refuses what would not render safely when a row is
saved (`clean()`, when what it renders changed), after each resource load, and
again before every render. It holds every list below, and the walk's bounds on
nodes, outputs, loops, includes, text and the rows it reaches.

## What a row and a value may vary in

Promise 1, a value never becomes markup, an attribute, a link target, CSS or
script, holds because every list a row or a value varies along is closed: each
admits a stated set and refuses the rest. The value properties
(`test_a_value_never_becomes_markup*`, read by html5lib as a browser reads a
mail) and the mutation script `scripts/messaging_templates_mutations.py` guard
the table as a whole.

| Dimension | The closed list | Enforced in | Test |
|---|---|---|---|
| Tags | `ALLOWED_TAGS`: `if`, `for`, `with`, `comment`, `block`, `include`, `extends`, `templatetag` | [engine](engine.py) `register`: anything else fails to compile | `test_the_engine_registers_exactly_the_allowlist_and_compiles_nothing_else` |
| Filters | `ALLOWED_FILTERS`: Django's thirteen and `url`; at most four to an expression; literal arguments | `register`; walker `_RowRules.expression` | `test_the_walker_refuses_each_rule` |
| Filter order and markup | `MARKUP_FILTERS` (`linebreaksbr`) the last filter of a printed value; `url` last, after `default` alone; `date` first; no other filter's result marked safe | `_RowRules.expression`; engine `_charged` | `test_a_cut_line_break_is_refused_since_markup_is_the_last_filter`, `test_only_a_markup_filter_returns_markup`, `test_no_chain_of_filters_lets_a_value_become_markup` |
| Literals | no `<`, `>` or `"`; at most 200 characters; never `_(…)` | walker `_literal_refusal` | `test_the_walker_refuses_each_rule` |
| A row's characters | none of `CONTROL_CHARACTERS` and no carriage return (rule 1); every save writes a carriage return as a newline, as `clean()` does; in no column a lone surrogate, a NUL, a value nested over 64 levels, what JSON cannot write (NaN, an infinity, over 4,300 digits), nor `None` where the column is not null, refused by `clean_fields()` before anything hashes or stores it | walker `_check`, `check_html` (`SOURCE_CONTROLS`), `MessageTemplate.clean_fields`, `MessageTemplate.clean` | `test_a_row_holds_no_control_character_and_a_return_is_a_newline`, `test_the_console_stores_a_carriage_return_as_a_newline`, `test_clean_raises_only_validation_error_and_what_it_admits_saves`, `test_a_nul_is_refused_by_full_clean`, `test_the_console_refuses_what_no_column_can_store` |
| HTML spelling | `SpellingRule` 2 to 6 and 8 | [HTML checks](html_checks.py) | `test_each_spelling_rule_refuses_with_its_message`, `test_the_strict_spelling_admits_what_every_reader_reads_alike`, `test_a_refusal_says_how_to_write_the_row` |
| Elements | `MAIL_ELEMENTS`, for start and end tags alike; `OFFICE_ELEMENTS` inside a layout's Outlook comment alone | HTML checks (rule 7) | `test_a_mail_is_written_with_its_own_elements_and_attributes` |
| Attributes | `MAIL_ATTRIBUTES`; a link attribute on the elements it links on alone (`LINK_ATTRIBUTES`: `href` on `a`, `src` on `img`, `background` on `body`, `table`, `td`, `th`, `cite` on `blockquote`) | HTML checks | `test_a_mail_is_written_with_its_own_elements_and_attributes`, `test_link_attributes_follow_url_and_text_attributes_are_text` |
| Where a value sits | text; `OUTPUT_ATTRIBUTES` (`title`, `alt`, `aria-label`); `dir`, `lang` and `xml:lang` the engine's own `{{ dir }}` and `{{ lang }}` alone (`ENGINE_OUTPUT_ATTRIBUTES`); a link attribute as one output whose last filter is `url`; a `style` attribute, `STYLE_OUTPUTS`; a `<style>`, `STYLE_ELEMENT_OUTPUTS`; a `<title>`, outputs and notes, no tag; never a comment | HTML checks `check_spans`, `check_link` | `test_the_html_checks`, `test_dir_and_lang_take_the_engines_own_outputs_alone`, `test_a_title_holds_outputs_and_comments_only` |
| A link a value gives | `LINK_SCHEMES`: an absolute `https` or `http` link whose authority is a domain name of two labels or more, the last a name, and a port, with no userinfo (`LINK_HOST`); a `mailto:` or `tel:` link; any other gives none | engine `url` | `test_a_url_value_gives_a_link_or_nothing` |
| A link an author writes | what `url` leaves unchanged, a `#fragment`, a `cid:` image, an `sms:` or `webcal:` link (`STATIC_LINK`) | HTML checks `check_link` | `test_a_static_link_is_absolute_or_a_fragment_or_a_cid_image` |
| Comments and declarations | in a layout alone: its opening `<!doctype html>`, `<!--[if mso]>…<![endif]-->` and the `<!--[if !mso]><!-->…<!--<![endif]-->` pair, static and balanced | HTML checks | `test_a_layout_holds_outlooks_conditional_comments_alone`, `test_only_a_layout_carries_outlooks_comments` |
| Character set | `utf-8` (`MAIL_CHARSET`) | HTML checks | `test_a_mail_declares_one_character_set`; the value properties read a mail's bytes in the character set it declares |
| A value's shape | the declared paths; a leaf a string, number, boolean or null | `TemplateWalk.prepare` | `test_required_values_and_shapes` |
| A value's characters | any but `UNPRINTED` (the control characters, and the bidirectional embeddings, overrides and isolates), which are dropped, and a lone surrogate, refused; the directional marks stay; at most 10,000 characters, 500 list items, 50,000 characters of JSON | `TemplateWalk.prepare` | `test_a_value_drops_its_control_characters`, `test_a_value_drops_bidirectional_overrides_and_keeps_its_marks`, `test_a_lone_surrogate_is_refused_as_invalid_values`, `test_variables_caps` |
| Numbers and dates | a number a float holds where `floatformat` or `pluralize` reads it; a date, or an aware datetime where a time is printed, where `date` reads it | `TemplateWalk.prepare` | `test_cheap_filters_spend_a_constant_and_a_raw_number_is_text`, `test_a_date_path_parses_iso_strings_and_refuses_what_is_not_a_date` |
| Conditions | at most `CONDITION_OPERATOR_LIMIT` (10) operators, each a node; a test reads a declared value, its length or a name the mail supplies; `in` takes a literal | walker | `test_a_condition_holds_at_most_ten_operators_each_a_node`, `test_condition_filters_refused` |
| Work | not a list but caps, each bounding the work where it applies: see the next table | walker, engine | the timed and counted cases named there |
| Markdown | raw HTML off; template spans whole; a link's target one output through `url`; what its `[` make the pass scan, `MARKDOWN_LABEL_SCAN_LIMIT` | `MarkdownFormatHandler` | `test_markdown_spans_reach_the_engine_whole`, `test_a_markdown_link_whose_label_and_target_are_values_is_admitted` |
| Subject and preheader | one line; the control characters and the bidirectional overrides dropped; every encoded-word opener broken to `= ?` (`ENCODED_WORD_OPENER`: `=?`, a charset, `?`, `b` or `q`, `?`, in either case, terminated or not, inside a word too); at most 1,000 characters | walker `_one_line` | `test_subject_and_preheader_drop_control_and_bidirectional_characters`, `test_a_subject_never_holds_an_encoded_word`, `test_a_subject_never_becomes_an_encoded_word` (the standard library's header machinery the judge) |
| Author CSS | not closed: the text an author writes in a `<style>` element or a `style` attribute. A value reaches it as the brand's outputs (above), or by the author's own choice: CSS reads an output's attribute (`content: attr(title)`, `[title^=…]` selectors) | — | — |

Work is bounded by caps, not by a list, and each cap bounds the work only where it
applies. What is guaranteed is the work, counted, which does not depend on the
machine: the caps below, and the counted work of
`test_the_costliest_preview_does_bounded_work`, `test_the_costliest_pass_does_bounded_work`
and `test_a_saving_walk_resolves_a_level_in_one_query`. The time is a
measurement: CPU seconds on an 18-core Apple M-series machine with CPython 3.14.9,
taken at the load each run states. `test_preview_time` and `test_pass_time` (under
`ANGEE_TIMED_TESTS=1`) state their bounds as measured there at a load near 13 (2 s
a preview, 1 s a pass) and scale them by a fixed reference workload measured in
the same process, so a busy machine slows the case and the bound alike.

| Where it applies | Caps | Pinned by |
|---|---|---|
| One row, before anything parses it | `SOURCE_LIMIT` (50,000 characters a column), `LINE_LIMIT`, `OPENERS_PER_LINE`, no opener that opens no template code, `NESTING_LIMIT`, `MARKDOWN_BRACKETS_PER_LINE`, `MARKDOWN_LABEL_SCAN_LIMIT` (50,000 characters of label scans, from the block pass alone); then `CONDITION_OPERATOR_LIMIT`, `FILTERS_PER_EXPRESSION`, `LITERAL_LIMIT` | `headings`, `rules`, `images`: refused before the Markdown pass; the construct table below |
| One form, straight after the Markdown pass | `FORM_SPAN_LIMIT` (2,000 spans), `TEXT_LIMIT` characters of text, `FORM_OPENERS_PER_LINE` | `headings`, `rules`, refused before the HTML checks read them |
| The walk, as it gathers rows | `WALK_SOURCE_LIMIT` (150,000 characters of source), each level counted from its rows' `text_length` (the database's `Length`, in characters) before their text is read, `description` and `sample` never read; each checked row's nodes, outputs and text against the tree's caps; what its rows' checks read, `WALK_READ_LIMIT` (600,000 characters of forms) and `WALK_SPAN_READ_LIMIT` (4,000 spans), comments included; no row fetched past `INCLUDE_DEPTH_LIMIT` includes below its tree's root; a saving walk's include targets resolved in one query a level | `quotes`, `single`, `fivedeep`, `thousand`: refused after one to three row checks; `combined`: refused at the read caps after four; a chain of 900 includes: 27 queries on the saving walk, 6 on the render-side judge (`test_a_chain_past_the_include_depth_is_refused_before_the_gather_reads_it`); a level of 600 parts of 48,999 characters: refused with none of its text read, in one query (`test_a_level_is_counted_before_its_text_is_read`); a tree of exactly 150,000 characters, astral ones among them, renders (`test_a_walk_at_the_source_cap_renders_and_one_character_more_is_refused_unread`); `test_a_saving_walk_resolves_a_level_in_one_query` (24 queries: a level is two, its sizes and rows, then its text) |
| Each tree a pass renders | `NODE_LIMIT`, `OUTPUT_LIMIT`, `TEXT_LIMIT`, `LOOP_DEPTH_LIMIT`, `INCLUDE_DEPTH_LIMIT` | `review` |
| Each pass | `RenderBudget`: 500 loop or include renders, 200,000 characters of loop output, 500,000 of filter input; a tenth for the subject and preheader | `q11b-combined`, `review`'s render, `review-subject` |
| The values | `prepare()` costs a constant per declared path per value; `VALUES_JSON_LIMIT`, `VALUE_STRING_LIMIT`, `VALUE_ITEMS_LIMIT` | `dated` |
| The call stack | the caps above keep the deepest admitted row (16 nested tags in each of four rows, three includes, two loops, a ten-operator condition) 254 frames over its caller's | `test_the_deepest_admitted_row_and_values_render` (under 300) |

The Markdown pass and the row checks scan per opener in these constructs, and nowhere else. Each row's worst cost
is measured on one row of 49 lines of 999 characters, the most the source caps admit, at load 49 to 52 on the machine
class above, beside the review case's heaviest row (its commented emphasis runs, 0.28 s):

| Construct | What scans per opener | Cap | Worst row |
|---|---|---|---|
| `[`, `![` (link and image labels) | markdown-it's `parseLinkLabel`, token by token to its `]` or the end of its paragraph, list item or heading | 30 a line; `MARKDOWN_LABEL_SCAN_LIMIT` a row | one `![` before a 49,000-character paragraph of emphasis runs: 0.34 s, admitted; thirty a line: refused before the pass |
| `[…]({{ … }})` (a link to an output) | the addon's `_template_link`, a `str.find` to the end of the line | 30 `[` a line | within the line above |
| `{{`, `{%`, `{#` (template spans) | the addon's `_template_span`, Django's `tag_re` to the end of the line | `OPENERS_PER_LINE` (30); none left unclosed | an unclosed one in a comment: refused before the pass |
| `` ` `` (code spans) | markdown-it's backticks rule, its closers cached | the source caps | 0.13 s |
| `<` (autolinks) | to the next `<` or `>` | the source caps | 0.18 s |
| `&` (entities), `\` (escapes) | a bounded pattern; one character | the source caps | 0.20 s; 0.18 s |
| `*`, `_` (emphasis runs) | markdown-it's `balance_pairs`, its openers bounded by run | the source caps | 0.28 s (the review row); `***` runs 0.23 s |
| link reference definitions | none: the rule is off | — | — |

The costliest admitted template found is `onebracket`: the review case's loop beside three commented parts of
emphasis runs, each opening on one `![`. It previews at 1.09 to 1.10 times the review case measured back to back
(2.03 to 2.06 s against 1.85 to 1.89 s at load 55 to 57); `test_preview_time` holds it under 1.3 times.

## Rendering

`row.render(values, kit=None)` walks the row, shapes `values` to what it
declares (`TemplateWalk.prepare`: undeclared keys dropped, every required value
present and not null, every declared path filled, ISO dates parsed at the paths
read through `date`), and renders it on Django's template engine in the row's
language: the subject and preheader on one line each, the HTML body escaping
every value, and the plain-text body from the rows' text forms, with the row's
`language` and its text `direction` (`TextDirection`: `rtl` or `ltr`, the one
the HTML's `dir` says), for whoever shows the subject, preheader or plain text outside the
HTML. A render reads
rows and the brand kit through the caller's scope, the default kit when `kit` is
`None`. It raises `ValidationError` and nothing else, and each item of the error
carries its code in `error_list`, so a sender tells them apart:
`invalid_values` (a value missing, misshapen, a lone surrogate, a number no
float holds where `floatformat` or `pluralize` reads it, a moment the local zone
cannot hold, a bare date printed with a time), `message_template_refused` (the
walk), `render_budget` (a pass's `RenderBudget`; the subject and preheader get a
tenth of a body's), `subject_too_long`, `no_brand_kit`, and `render_failed` for
any other failure, which is logged with its traceback first. Every string value,
and the subject and preheader, drop the control characters and the bidirectional
overrides and isolates (`UNPRINTED`). A sender picks the row with
`effective("template", key, person=recipient)`, renders it as the system actor,
and sends the HTML part as `charset=utf-8`, the one character set a row
declares. The subject the renderer returns is a plain string a sender passes to
its mail library as it is: it holds no line break, no control character, no
bidirectional override and no encoded-word opener, so a mail library stores
exactly the string it was given. A sender never compiles rendered output as a
template again, since a value may spell `{{ … }}`.

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
fingerprints its kind and what it renders as stored; a save naming some of those
columns hashes the stored values of the rest, read under a lock. A load saves rows in file order and a
save walks what the row includes, so a template's layout and every block or part
a row includes load before it (`depends_on`).

A baseline is walked among the shipped rows alone, when it saves and again after
every load, so a shipped defect fails the load. After a load each baseline is
also judged among the rows that render: a refusal reached only through the
deployment's own row or override, one the shipped row includes or is laid out
by, does not fail the load. It is logged as a warning on the
`angee.messaging_templates.models` logger naming the shipped row, the
deployment's row and the refusal, which an operator sees in the load command's
error output unless the host configures logging otherwise; the shipped row
refuses at render until the deployment fixes its row. Saving an included row
does not judge the rows that include it.

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
