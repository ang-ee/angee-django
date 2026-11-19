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
  it is made. While unpublished it may be revised in place; `publish()` freezes
  it, and a change after that is a new version. A response pins the version it
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
  panels and pages lay them out. There are no expressions (`visibleIf` and the
  like): a definition that carries one is refused.
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
already made. A form that takes no submission raises `FormUnavailable`; answers
to a version the form no longer serves raise `FormChanged`.

Response rows are closed to generic edits and deletion. Only the owner's verbs
change one:

- `deliver()` hands it to the form's target and records the outcome
  (`delivery`: pending, delivered, failed, dismissed). The target's verb, the
  link to the record it made (`ResponseRecord`) and the delivered state are one
  transaction; a failure leaves no record behind and is recorded in
  `delivery_error`, where staff see it. A response that already has its link is
  never delivered twice.
- `dismiss()` gives up on one that could not be delivered.
- `erase()` blanks the answers its version marks as personal and keeps the row.
- `Response.objects.purge_expired(now=…)` removes delivered responses past
  their form's `retention_days`; a daily task calls it.

Delivery runs on a worker after the submission commits
(`webforms.deliver_response`), retried with backoff.

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
outcome. It returns the record it made or found, or `None`. The web forms addon
never names another addon's model.

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
