# Web forms

Public forms and surveys. A form's questions are stored as versions in every
language it is written in, each submission is kept as a response before anything
is made from it, and a target the form selects turns a response into a record of
the addon that owns it.

## A form, its versions and its addresses

- A `Form` is what staff manage: its name, whether it takes submissions
  (`status`: draft, published, closed; `open()` and `close()`), the version the
  public answers, its target, the theme its public page wears and how long a
  delivered response is kept.
- A `FormVersion` holds the questions. It is numbered after its form's last when
  it is made. While unpublished it may be revised in place; `publish()` judges
  and freezes the stored row under its lock, and a change after that is a new
  version, through every manager. A response pins the version it
  answered, so its answers stay readable whatever the form asks later.
- A `FormAlias` is a public address of a form (a unique lower-case slug),
  optionally in one language.

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
  spelling; nothing is rewritten. Every other text is plain and is shown as
  written.
- A choice is `{"value": <code>, "text": <text>}`. Answers store the code, never
  the label, so they do not depend on language. "Other" is the code `other`,
  with its text in `<name>-Comment`.
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
- `Response.objects.purge_expired(now=…)` removes delivered responses past
  their form's `retention_days`; a daily task calls it.

Delivery runs on a worker after the submission commits
(`webforms.deliver_response`). A failed delivery, or one whose task was lost, is
queued again by `webforms.redeliver_responses` every five minutes when its next
try has come (`retry_at`): five minutes after the first failure, doubling to six
hours, eight attempts in all. After that the response stays failed until staff
deliver or dismiss it.

## The public address

A form answers at `forms/w/<alias>` (`PUBLIC_PREFIX` in [`urls.py`](urls.py)).
The prefix is an interim one: `forms/<slug>` is messaging's public form path
until that path is removed, and one path has one owner.

- `GET` describes the version the form serves now: `form`, `alias`, `version`
  (its number), `definition`, `languages`, `default_language`, `language` (the
  alias's, or empty), `theme` and `honeypot_field`. It is never cached. An alias
  that leads to no form taking submissions is not found.
- `POST` keeps one submission. The body is a JSON object with exactly
  `submission_id`, `version`, `language`, `answers` and the honeypot field.
  The shared guard (`angee.server.ingress`) runs first: JSON only, a body cap, a
  rate limit per sender and address, no undeclared key, the honeypot, an
  optional token hook. Then `Response.objects.receive` keeps it.
  - `202 {"submission_id"}`: kept, or already kept under that id. A filled
    honeypot gets the same answer and keeps nothing.
  - `400 {"error": "invalid_submission", "fields": {<name>: [messages]}}`: each
    refusal under its question's name, or under `version` or `submission_id`.
  - `409 {"error": "form_changed"}`: the answers are to a version the form no
    longer serves; the page reloads the definition.
  - A refusal by the guard is `{"error": <code>, "message"}` with its status
    (`413`, `415`, `429` with `Retry-After`, `403`, `503`).

The bounds are the `ANGEE_WEBFORM_*` settings (body bytes, rate-limit burst and
window, honeypot field, token hook).

## What a response becomes

A form selects one target by key (`Form.target_class`). `store`, the default,
keeps the response as the record. An addon that owns a record contributes its
own with one settings line and a class that calls its owner's verb:

```python
# <addon>/autoconfig.py
SETTINGS = {"ANGEE_WEBFORM_TARGET_CLASSES.enquiry": "acme.enquiries.targets.EnquiryTarget"}
```

```python
# <addon>/targets.py
from angee.webforms.targets import FormTarget


class EnquiryTarget(FormTarget):
    key = "enquiry"
    label = "File an enquiry"
    config_model = EnquiryMapping  # a Pydantic model: which answer fills which field

    def deliver(self, response):
        return Enquiry.objects.file(subject=response.answers[self.config.subject], …)
```

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
- A response carries no history, so an erased answer is kept nowhere.
- The link to the record a response became follows that record through a merge
  and goes when the record or the response does; it never blocks either.

## Permissions

`webforms/form` is managed by platform admins and the `webforms_admin` role of
`webforms/role`. A version, an alias and a response are read and written
through their form. Nobody creates a response under an actor: a submission is
kept as named system work.
