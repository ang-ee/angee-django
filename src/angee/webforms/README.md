# Web forms

Public forms and surveys. A form's questions are stored as versions in every
language it is written in, each submission is kept as a response before anything
is made from it, and a target the form selects turns a response into a record of
the addon that owns it.

## A form, its versions and its addresses

- A `Form` is what staff manage: its name, whether it takes submissions
  (`status`: draft, published, closed; `open()` and `close()`), the version the
  public answers, its target, the theme its public page wears and how long a
  response is kept.
- A `FormVersion` holds the questions. It is numbered after its form's last when
  it is made. While unpublished it may be revised in place; `publish()` judges
  and freezes the stored row under its lock, and a change after that is a new
  version, through every manager. A response pins the version it
  answered, so its answers stay readable whatever the form asks later.
- A `FormAlias` is a public address of a form (a unique lower-case slug),
  optionally in one language.

## A form an addon declares

An addon that ships a form declares it as one row of its resources, in a
`<NNN>_webforms.form.yaml` file its `[tool.angee.resources]` lists. The row names
the form's fields, the content of the version it serves (`version`, the fields of
`FormVersion.CONTENT_FIELDS`) and the slugs it answers at (`aliases`). Here the
addon `acme` ships the webform channel its form posts on as well, and names it by
xref:

```yaml
# acme/resources/master/010_integrate.vendor.yaml
rows:
  - xref: vendor
    fields: {slug: webform, display_name: Public webforms}
```

```yaml
# acme/resources/master/020_messaging.channel.yaml
rows:
  - xref: intake
    fields: {display_name: Intake, vendor: acme.vendor, backend_class: webform}
```

```yaml
# acme/resources/install/030_webforms.form.yaml
rows:
  - xref: intake_form
    fields:
      name: Intake
      target_class: message
      target_config:
        channel: acme.intake        # the channel's xref; stored as its public id
        slug: intake
      theme_id: acme.paper          # optional
      retention_days: 365           # optional, and create_only
      aliases: [intake]
      version:
        definition:
          elements:
            - {type: text, name: name, isRequired: true, title: Your name}
            - {type: text, name: email, inputType: email}
        languages: [en]
        default_language: en
        personal_questions: [name, email]
        contact_question: email
```

```toml
[tool.angee.resources]
master = [
  { path = "master/010_integrate.vendor.yaml", adopt = "slug" },
  { path = "master/020_messaging.channel.yaml", depends_on = "master/010_integrate.vendor.yaml" },
]
install = [
  { path = "install/030_webforms.form.yaml", depends_on = "master/020_messaging.channel.yaml",
    create_only = ["retention_days"] },
]
```

[`FormResource`](resources.py) saves the form and hands the rest to the form's
verbs, inside the load's one transaction and its seed changeset. A load runs as
system work; `Resource.objects.load_xref(…, actor=…)` saves the row as its named
actor, so the form's own checks ask that actor, and choosing the target asks the
target's `permits`:

- **Identity** is the row's xref in the resource ledger, never an alias. Staff
  may rename or delete an alias; the next load neither fails nor makes a second
  form.
- **Every row states its `version`.** A row without one is refused under
  `version`, whether it would make the form or change it.
- **An unchanged row is skipped**, by the ledger's content hash, so what staff
  changed since stands: a closed form, a version they published, an alias they
  renamed or deleted.
- **A changed row** applies the form fields it names (`name`, `target_class`,
  `target_config`, `theme_id`) and serves its `version` (`Form.serve`). The
  version served now stays when it holds exactly that content; otherwise a new
  version holding it is made and published (`FormVersion.publish`). Content the
  form served before, such as a declaration returned to its earlier questions, is
  published again as a new version: the declaration, not an older version, says
  what is served. Publishing opens a draft form; a closed form stays closed. A
  version the publication refuses, or one its fields cannot hold, fails the load
  and leaves nothing: the ledger keeps the declaration that last applied, so the
  next load tries again.
- **Any change of the row serves its declared content again.** A row that
  changes only `name` or `theme_id`, reorders `aliases`, or whose entry gains a
  `create_only` field other than `version`, still serves its `version`: a
  version staff published since is replaced by the declared one. An entry that
  leaves the questions to staff once the form exists lists `version` under
  `create_only`; the row still states it, the form is made serving it, and no
  later load serves it again.
- **`aliases`** are added when the form lacks them (`Form.ensure_aliases`), when
  the form is made and on every changed row; staff's own aliases stay, and the
  declaration never removes one. A slug another form holds refuses the load,
  also when the other form takes it at the same moment.
- **`retention_days` is create-only.** A form's retention is what it tells its
  respondents and removes responses when it passes, so once the form exists it is
  the deployment's: a file that declares it lists it under the entry's
  `create_only`, and the load refuses one that does not. Every other form field is
  applied on every changed row; an entry may list it under `create_only` too.
- **`target_config`** names records by xref where its target's `config_model`
  declares a relation (`json_schema_extra={"relation": {"resource": "<app.Model>"}}`):
  each resolves to its record's public id when the row applies, through the
  resource ledger and among the load's own addons, as the row's relation columns
  do (`Resource.objects.resolve_config_references`). A public id stays as
  written. An xref that names nothing, or a record of another kind, refuses the
  load under `target_config`, and so does a configuration the target refuses
  (`FormTarget.clean`): the `message` target takes only a webform channel. Web
  forms reads only the target's schema; what the record is stays the target's.

**A form that exists before its declaration** (made by the
`webforms_messaging` migration template, or by the addon's own code) is not
adopted by itself: a form has no adoptable key, and the declared form is refused
while the earlier one posts on the same channel and slug. Bind the earlier form
to the row's xref once, after it exists and before any load that carries the
row. The `source` is the entry's ledger path, its manifest path under
`resources/`:

```python
# manage.py shell
from django.apps import apps
from rebac import system_context

with system_context(reason="acme: adopt the intake form"):
    apps.get_model("resources", "Resource").objects.bind_instance(
        addon=apps.get_app_config("acme"), xref="intake_form", instance=earlier_form,
        source="resources/install/030_webforms.form.yaml",
    )
```

Sequence the releases so the bind falls between them: `provision` runs
`resources load` right after `migrate`, so a release that ships the row loads it
before anyone could bind. When the move template makes the earlier form, ship
the template in one release, bind once it has applied, and ship the row in a
later release. A form the addon's own code made exists already: bind it before
deploying the release that ships the row.

The next load then treats the row as changed: it applies the declared fields
(not a create-only `retention_days`), serves the declared content as a new
version unless the form serves it already (or the entry lists `version` under
`create_only`, which leaves the earlier form's version served), and adds the
declared aliases beside the form's own.

A consumer links to the form through `publicAddress(slug)`, which `@angee/webforms`
exports: the one place the web side names where a form answers.

## The stored definition

A version's `definition` is a checked subset of a
[SurveyJS](https://surveyjs.io/form-library/documentation/overview) survey
definition. The subset is the contract:
[`schemas/definition.schema.json`](schemas/definition.schema.json) owns the
vocabulary, and [`validators.py`](validators.py) refuses what a schema cannot
say and derives, from one definition, the JSON Schema of the answers the server
accepts.

- Question types: `text` (text, email, tel, url, number, date), `comment`,
  `radiogroup`, `dropdown`, `checkbox`, `boolean` and `rating`; `html` blocks,
  panels and pages lay them out. Panels nest three deep. There are no
  expressions (`visibleIf` and the like): a definition that carries one is
  refused.
- Two texts are markup: an `html` block's `html` and the form's
  `completedHtml`. They are shown as HTML on a public page of the application's
  origin, so only simple markup is stored: paragraphs, line breaks, emphasis,
  lists, headings and links to an `http`, `https` or `mailto` address. Anything
  else (a script, a style, an image, an event handler, a comment) is refused
  when the version is saved, and so is markup not written in its one plain
  spelling; nothing is rewritten. Curly brackets are refused in markup: the
  page's library fills `{name}` with an answer, which markup would then read as
  markup. Every other text is plain and is shown as written.
- A `text` question may carry `autocomplete`, the browser's autofill hint
  (`name`, `given-name`, `family-name`, `email`, `tel`, `organization`, the
  address hints, `off`); payment and credential hints are not offered. A form
  with pages may show its progress (`showProgressBar`, `progressBarLocation`,
  `progressBarType`). `showTitle: false` keeps a form's `title`, in each
  language, as the page's document title without rendering it as a heading,
  for a form whose first block carries its own.
- A choice is `{"value": <code>, "text": <text>}`. Answers store the code, never
  the label, so they do not depend on language. "Other" is the code `other`,
  with its text in `<name>-Comment`.
- No element is named `submission_id`, `version`, `language` or `answers`: a
  refused submission reports each refusal under the name it is about, an
  envelope key or a question.
- Every text is one string, the same in every language, or a map with the
  form's default language under `default` and each other language under its
  tag: `{"default": "Your name", "es": "Tu nombre"}`. The version records
  `languages` and `default_language`.
- An answer is a coded choice, a bounded string, a number or a boolean, under
  its question's name. It can never name a record or carry an instruction.

A version is published only when its definition is inside the subset, every
text map carries every language of the form, and it has stated which questions
hold personal data.

## Responses

A `Response` is evidence: the answers as received, the language, the client's
`submission_id` and the version. `Response.objects.receive(form, version=…,
submission_id=…, language=…, answers=…)` keeps one, once per form and
`submission_id`: a client that sends the same id again gets the response it
already made, even after the form closed. A form that takes no submission
raises `FormUnavailable`; answers to a version the form no longer serves raise
`FormChanged`. The answers are judged before the form's row is locked, so a
slow submission delays no other.

Response rows are closed to generic edits and deletion. Only the owner's verbs
change one:

- `deliver()` hands it to the form's target and records the outcome
  (`delivery`: pending, delivered, failed, dismissed). The target's verb, the
  link to the record it made (`ResponseRecord`) and the delivered state are one
  transaction; a failure leaves no record behind. A response that already has
  its link is never delivered twice.
- A failure raises `DeliveryFailed` and is kept in `delivery_error`, where staff
  see it, by its class alone (`RuntimeError`). A failure's message and the
  values in its frames may quote an answer, so they are neither stored, logged
  nor raised; the log says where it was raised.
- `dismiss()` gives up on one that could not be delivered.
- `erase()` blanks the answers its version marks as personal and keeps the row.
- `Response.objects.purge_expired(now=…)` removes every response received
  longer ago than its form's `retention_days` (one day to a century; empty
  keeps them), delivered or not; a daily task calls it. A form's retention is
  what it tells its respondents, so it holds whether or not delivery succeeded.
  A submission is never lost unseen for that: the console counts a form's
  pending and failed responses and shows when the first of them expires
  (`undelivered_count`, `undelivered_expires_at`), each response shows its
  expiry (`expires_at`), and every retry of a delivery has been made within
  hours, long before the shortest retention; a response past its retention is
  not queued again.

Delivery runs on a worker after the submission commits
(`webforms.deliver_response`). A failed delivery, or one whose task was lost, is
queued again by `webforms.redeliver_responses` every five minutes when its next
try has come (`retry_at`): five minutes after the first failure, doubling to six
hours, eight attempts in all. An attempt is counted, and its next try set,
before the target runs, so an attempt whose worker dies counts too. After the
last one the response stays failed until staff deliver or dismiss it.

## The public address

A form answers at `forms/<alias>` (`PUBLIC_PREFIX` in [`urls.py`](urls.py)).

- `GET` describes the version the form serves now: `form`, `alias`, `version`
  (its number), `definition`, `languages`, `default_language`, `language` (the
  alias's, or empty), `theme` and `honeypot_field`. It is never cached. An alias
  that leads to no form taking submissions is not found.
- `POST` keeps one submission. The body is a JSON object with exactly
  `submission_id`, `version`, `language`, `answers` and the honeypot field.
  The shared guard (`angee.server.ingress`) runs first: JSON only, a body cap, a
  rate limit per sender and address, no undeclared key, the honeypot, an
  optional token hook. Then `Response.objects.receive` keeps it.
  - `202 {"submission_id"}`: kept, or already kept under that id. A resend gets
    this answer and nothing more: never the answers it sent before, nor an id of
    the response. A filled honeypot gets the same answer and keeps nothing.
  - `400 {"error": "invalid_submission", "fields": {<name>: [messages]}}`: each
    refusal under its question's name, or under `version` or `submission_id`.
  - `409 {"error": "form_changed"}`: the answers are to a version the form no
    longer serves; the page reloads the definition.
  - A refusal by the guard is `{"error": <code>, "message"}` with its status
    (`413`, `415`, `429` with `Retry-After`, `403`, `503`).

The addon declares the bounds, and a deployment may change them:

| Setting | Default | Bounds |
|---|---|---|
| `ANGEE_WEBFORM_MAX_BODY_BYTES` | `65536` | The size of one request body |
| `ANGEE_WEBFORM_RATE_LIMIT_BURST` | `10` | Submissions per sender and address in one window |
| `ANGEE_WEBFORM_RATE_LIMIT_WINDOW` | `60` | The window, in seconds |
| `ANGEE_WEBFORM_HONEYPOT_FIELD` | `_website` | The decoy field; empty turns it off |
| `ANGEE_WEBFORM_TOKEN_HOOK` | empty | A dotted `(request, payload) -> bool` that admits a submission |

A token hook that cannot be loaded closes every form (`503`) and fails the
system check `angee.E033`.

A form that has closed is not described and takes nothing new, but a resend of
a submission it kept is still answered `202 {"submission_id"}`:
`Response.objects.receive` decides, and the view resolves the address alone.

## The public page

A browser that opens `forms/<alias>` gets a page of its own, not the
application. It is a standalone page: the addon's web package declares it
(`angee.pages` in `web/package.json`, on the paths under the same prefix), and
the ingress serves it to a request that asks for HTML. It loads nothing of the
application. Everything else at that address reaches Django: the page asks it
for the JSON description above and posts the submission to it.

## What a response becomes

A form selects one target by key (`Form.target_class`). `store`, the default,
keeps the response as the record. An addon that owns a record contributes its
own with one settings line and a class that calls its owner's verb:

```python
# <addon>/autoconfig.py
SETTINGS = {"ANGEE_WEBFORM_TARGET_CLASSES.enquiry": "acme.enquiries.targets.EnquiryTarget"}
```

```python
# <addon>/permissions.extends.zed: the type the target makes
definition webforms/response_record {
    relation enquiry: acme/enquiry // rebac:field=record
}
```

```python
# <addon>/targets.py
from angee.webforms.targets import FormTarget


class EnquiryTarget(FormTarget):
    key = "enquiry"
    label = "File an enquiry"
    config_model = EnquiryMapping  # a Pydantic model: which answer fills which field

    def permits(self, actor):
        return Enquiry(queue=self.config.queue).with_actor(actor).has_access("create")

    def deliver(self, response):
        return Enquiry.objects.file(subject=response.answers[self.config.subject], …)
```

`permits` says who may point a form at the target, as it is configured. A target
makes its owner's records as system work, for anyone who submits the form, so
managing forms is not enough to choose one: the owner answers with its own
access check. It is asked when a form's target or its configuration changes
under an acting user, and every target states it. A target may also refuse a
configuration its owner will never take (`clean`, raising `ValidationError`):
`Form.clean` asks it when the target or its configuration changes and the form
is validated (`full_clean`), as the console and the resource loader do, for an
acting user and for system work alike. A plain `save()` does not ask it. A
collection edit (`Form.objects.filter(…).update(target_class=…)`) is refused:
the owner is asked one form at a time. What `deliver` returns must be a record of a type the
target's addon declared on `webforms/response_record`; anything else fails the
delivery, since nothing would erase the answers when it goes. Only declared
types carry the link in their metadata and pay for it when they are deleted or
merged.

`deliver` runs as named system work inside the transaction that records its
outcome. It returns the record it made or found, or `None`. When the owner
will not take the response as it is, it raises `DeliveryRefused("<code>")`: the
code (`unknown_category`) is all that is kept of the refusal, and a refused
response is not tried again by itself. The web forms addon never names another
addon's model.

## Personal data

- A response holds no address of its sender and no browser detail.
- Each version states the questions that hold personal data
  (`personal_questions`: null is not yet stated, an empty list states none) and
  may name the one whose answer finds a person's responses
  (`contact_question`).
- A response carries no history, and a failed delivery keeps only the
  failure's class, so an erased answer stays nowhere inside web forms. What a
  target made from a response is a copy this addon does not reach: the record a
  response became, or the message a form posted, is its owner's to erase.
- A response keeps its contact answer beside the answers (`contact`: trimmed,
  in one letter case), and the contact question is always one the version
  states personal. `Response.objects.answered_by("<contact>")` finds a person's
  responses across forms by it, and the console's responses list filters on
  it: that is how a person's request to be erased is served, one Erase per
  response found. Erasing blanks it with the answers.
- `Response.objects.erase_for_record(record)` blanks the personal answers of the
  responses a record was made from: the owner of that record calls it when it
  erases the person.
- The link to the record a response became is evidence of what was made, so a
  merge of that record rewrites nothing: the survivor's responses are read
  through it, and a response's record is read as its survivor. The link goes
  when the record or the response does and never blocks either. When the record
  is deleted, the response it was made from loses its personal answers first.
- A form is deleted, with its versions, only while it holds no response; a form
  that has been answered is archived, and its responses go when their retention
  has passed.

## Permissions

`webforms/form` is managed by platform admins and the `webforms_admin` role of
`webforms/role`. A version, an alias and a response are read and written
through their form. Nobody creates a response under an actor: a submission is
kept as named system work.
