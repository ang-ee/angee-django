# HubSpot for parties

`angee.parties_integrate_hubspot` reads one HubSpot account's CRM contacts one
way, from HubSpot into `angee.parties`. It registers the `hubspot` directory
backend and the `hubspot` vendor row. A HubSpot directory has one address book
holding every contact. Each contact lands as a person with its names, emails,
phones, employer and postal address, and HubSpot's later edits follow on each
sync. A job title lands only beside a company, because parties keeps a title on
the employment. A person's own edits outrank HubSpot's. A contact HubSpot merges
lands on the person of a record it replaced.

A contact HubSpot archives, deletes or merges away leaves HubSpot's list of
contact ids, which the daily absence sweep walks: the first sweep that misses it
marks its link unavailable, the second withdraws what it stated. A read of the
contact itself withdraws it at once, but only on HubSpot's word: a batch read
that names it not found, or answers a merge's survivor that names it replaced.
That read happens when a contact whose sync failed is read again, and when a
sweep reads the contacts its walk listed. A read that merely leaves a contact
unanswered withdraws nothing; it fails and is tried again. A walk that lists no
contact at all is believed only when HubSpot's search counts none either. A walk
cut short part-way cannot be told from a complete one, so a contact it misses
waits for the second sweep. In every case the person stays.

A directory reads with a static-token credential holding a HubSpot service key
that has the `crm.objects.contacts.read` scope. The backend admits no other kind
and no empty token, and connects through no OAuth client. Connection discovery
checks the key and learns the account it reads, so a directory syncs only once
discovery has finished, and its source links open each contact in HubSpot when
HubSpot states the account's web host. Once a directory has learned its account,
discovery refuses a key of another account. The connection test asks HubSpot for
one contact, and it refuses a rejected key and a key without the scope with
separate messages.

A platform admin connects an account from the console's Directories page: the
addon's web fragment adds "HubSpot account" to the Connect menu
(`parties.directories#toolbar`), a dialog that takes a name and the service key
in a masked field and sends the key as entered. Its `connect_hubspot_directory`
mutation calls `Directory.objects.connect`, which tests the key with HubSpot
before it saves the credential and the directory, so a rejected key saves
nothing and shows HubSpot's refusal. Each directory gets a credential of its own.
Discovery then learns the account, and the first sync follows.

A sync after a baseline searches for contacts changed since the last one, and
reaches five minutes further back, since HubSpot's search index shows a change a
few moments late, only until a sync has run that long after the newest change it
read. A poll of an account where nothing changed then sends one search and reads
nothing. Each pass keeps HubSpot's own time, from the `Date` of its answers, so the
host's clock never decides what a pass reads.

The backend speaks HubSpot's date-versioned REST API through integrate's pinned
request (`HttpClient.request`), with no HubSpot SDK;
[the stack guide](../../../docs/stack.md#backend) records why. Integrate's
`HttpRefusal` classifies every refused request, and `HubSpotError` adds only
HubSpot's own cases: a 423 is transient, a rate limit without `Retry-After`
waits for the window of the policy HubSpot names, and the daily limit says so.
When HubSpot rejects a key that lacks a service key's usual shape, the message
says to check it for extra or missing characters; the shape never refuses a key
before HubSpot sees it. `HubSpotDirectoryBackend` composes the parties directory base,
`HubSpotClient` takes the authorization headers its caller gives it and never
reads a credential, `HubSpotCursor` holds a stream's position inside a baseline
or delta pass, and `contact_from_properties` maps HubSpot's standard contact
properties onto the parties neutral contact. Their docstrings own the contracts.

## Mapping custom properties

A consumer addon fills its own person columns from HubSpot contact properties,
custom ones especially, with a parties directory mapping (`DirectoryMapping` in
`angee.parties.backends`, which owns the contract; see the
[parties README](../parties/README.md)). Its donor of `parties.Person` declares
the columns and the statement group that owns them; its mapping names the
`hubspot` backend and the properties' internal names, and is registered under its
key from the consumer's `autoconfig.py`:

```python
from angee.parties.backends import DirectoryMapping
from angee.parties_integrate_hubspot.client import parse_bool, parse_number, parse_options, parse_string


class ExampleContactMapping(DirectoryMapping):
    key = "example_hubspot"
    backend = "hubspot"
    fields = ("preferred_colour", "lucky_number", "contact_channels", "do_not_record")
    version = 1

    def project(self, row):
        if parse_bool(row.get("do_not_record")):
            return {"colour": "", "number": None, "prefers_email": False}
        number = parse_number(row.get("lucky_number"))
        return {
            "colour": parse_string(row.get("preferred_colour")),
            "number": None if number is None else int(number),
            "prefers_email": "email" in parse_options(row.get("contact_channels")),
        }

    def apply_record(self, person, values, *, link):
        person.statements_for("example").set(
            aspect="example", values=values, asserted_by=link.asserting_source(), link=link
        )


# autoconfig.py
SETTINGS = {"ANGEE_DIRECTORY_MAPPING_CLASSES.example_hubspot": "example.contacts.mappings.ExampleContactMapping"}
```

HubSpot states every value as a string or null, and the `parse_*` functions of
`angee.parties_integrate_hubspot.client` read them by property type, so no
mapping re-derives HubSpot's encodings: `parse_string` (text and single choice),
`parse_options` (multiple choice, joined with `;`), `parse_number` (an exact
`Decimal`), `parse_bool`, `parse_datetime` (an instant in UTC) and `parse_date`
(the day HubSpot states as its midnight in UTC). Each answers `None` for an unset
value and raises `ValueError` for a value not of its type; the mapping's author
picks the parser by the property's type, and the backend reads no property
definitions. `project` returns JSON values, so it turns a number, a day or an
instant into the JSON form its column takes.

The backend asks HubSpot for a mapped property only while a mapping names it,
on every read of contacts, and hands HubSpot's values to the base, which keeps
only the projections, compares them and applies them after each contact's
ingest. Registering, removing or re-versioning a mapping replays every contact
once. HubSpot ignores a property name the account does not define, so a misspelt
or deleted property reaches `project` as not sent (absent from `row`, where an
unset one is `None`) and the sync goes on: a mapped column that stays empty
means the property's internal name needs checking in HubSpot.

## Tests

`angee.parties_integrate_hubspot.testing.server` ships `HubSpotServer`, an
in-memory HubSpot account behind `angee.integrate.testing.http.mock_transport`.
Use it with `serve(server)` or the `hubspot_server` fixture, and script records,
merges, deletions, HubSpot's clock (each answer's `Date`), index lag, refusals
and per-record read errors on it. Nothing calls the real HubSpot API.
