# HubSpot for parties

`angee.parties_integrate_hubspot` reads one HubSpot account's CRM contacts one
way, from HubSpot into `angee.parties`. It registers the `hubspot` directory
backend and the `hubspot` vendor row. A HubSpot directory has one address book
holding every contact. Each contact lands as a person with its names, emails,
phones, employer and postal address, and HubSpot's later edits follow on each
sync. A job title lands only beside a company, because parties keeps a title on
the employment. A person's own edits outrank HubSpot's. A contact HubSpot merges
lands on the person of a record it replaced. Other HubSpot objects, deals and notes among
them, are read only for a consumer addon that maps them
([Mapping another HubSpot object](#mapping-another-hubspot-object)).

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

A contact HubSpot states in a shape the backend cannot read (a property value that
is not a string, say) is that contact's failure alone: it waits under its id
while the rest of its page lands, and lands once HubSpot states it readably. A
record without a usable id cannot be told apart from any other, so it is logged
and skipped.

A directory reads with a static-token credential holding a HubSpot service key
that has the `crm.objects.contacts.read` scope. The backend admits no other kind,
no empty key and no credential that needs reconnecting, and connects through no
OAuth client. It judges the key as the request sends it, unstripped. Connection discovery
checks the key and learns the account it reads, so a directory syncs only once
discovery has finished, and its source links open each contact in HubSpot when
HubSpot states the account's web host. Once a directory has learned its account,
discovery refuses a key of another account. The connection test refuses a
credential that needs reconnecting without a request; otherwise it asks HubSpot
for one contact, and it refuses a rejected key and a key without the scope with
separate messages.

A platform admin connects an account from the console's Directories page: the
addon's web fragment adds "HubSpot account" to the Connect menu
(`parties.directories#toolbar`), shown to whoever may create a directory, through
the parties connect dialog (`ConnectDirectoryAction`). The dialog takes a name and
the service key in a masked field and sends the key trimmed of surrounding
whitespace. Its `connect_hubspot_directory(name, service_key)` mutation calls
`Directory.objects.connect`, which tests the key with HubSpot
before it saves the credential and the directory, so a rejected key saves
nothing and shows the message for HubSpot's refusal. Each directory gets a
credential of its own.
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
says to check it for extra or missing characters. The shape itself refuses no
key. Before HubSpot sees a key, admission refuses an empty one, which has nothing
to send, and integrate's HTTP client refuses to send one a request header cannot
carry, such as a key holding a line break. Operator messages are the addon's and
integrate's own words: none repeats HubSpot's message or its error category. The
only facts from HubSpot they state are an answer's HTTP status and, when
discovery refuses a key of another account, the two account ids.
`HubSpotDirectoryBackend` composes the parties directory base, `HubSpotClient`
sends through the backend's HTTP client with the authorization headers the
backend gives it and never reads a credential, `HubSpotCursor` holds a stream's position inside a baseline
or delta pass, and `contact_from_properties` maps HubSpot's standard contact
properties onto the parties neutral contact. Their docstrings own the contracts.

A contact's person carries the time HubSpot says the record was created (its
`createdAt`, which every read states): a person the contact makes takes it as its
`created_at`, and a contact landing on a person that exists moves the person's
earlier when it is earlier, never later (`Party.backdate_creation`). A record read
before a contact carried that time has none in its stored payload; provision's
`backdate_parties_from_sources` counts it, and the record's next read from HubSpot
stores it.

## Mapping custom properties

A consumer addon fills its own person columns from HubSpot contact properties,
custom ones especially, with a parties directory mapping (`DirectoryMapping` in
`angee.parties.backends`, which owns the contract; see the
[parties README](../parties/README.md)). Its donor of `parties.Person` declares
the columns and the statement group that owns them. Its mapping names the
`hubspot` backend, the properties' internal names and, as `aspects`, the donor
groups it states: at least one, and never a group the contact ingest states, both
checked at startup; a mapping that states a group it does not declare fails the
page. It is registered under its key from the consumer's `autoconfig.py`:

```python
from angee.parties.backends import DirectoryMapping
from angee.parties_integrate_hubspot.client import parse_bool, parse_number, parse_options, parse_string


class ExampleContactMapping(DirectoryMapping):
    key = "example_hubspot"
    backend = "hubspot"
    fields = ("preferred_colour", "lucky_number", "contact_channels", "do_not_record")
    aspects = ("example",)
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
definitions. A `ValueError` that leaves `project` refuses that one contact,
naming the mapping and never the value, while the rest of its page lands; the
stream's rescan retries the contact, so it lands once HubSpot states a value of
the type or the mapping is fixed to read it. Any other error is a bug and fails
the page. `project` returns JSON values, so it turns a number, a day or an
instant into the JSON form its column takes.

The backend asks HubSpot for a mapped property only while a mapping names it,
on every read of contacts, and hands HubSpot's values to the base, which keeps
only the projections, compares them and applies them after each contact's
ingest. Registering, removing or re-versioning a mapping replays every contact
once, and the replay withdraws a removed mapping's statements. HubSpot ignores a
property name the account does not define, so a misspelt or deleted property
reaches `project` as not sent (absent from `row`, where an unset one is `None`)
and the sync goes on: a mapped column that stays empty means the property's
internal name needs checking in HubSpot.

## Mapping another HubSpot object

What an account's other objects mean is its owner's business: one account keeps
sales deals, another relabels deals as something else entirely. So the addon
reads another object type as plain records, and a consumer addon decides what
they mean with a `HubSpotObjectMapping` (`angee.parties_integrate_hubspot.backends`,
whose docstring owns the contract), registered under its key from the
consumer's `autoconfig.py`:

```python
from angee.parties_integrate_hubspot.backends import ContactNotLanded, HubSpotObjectMapping
from angee.parties_integrate_hubspot.client import parse_string


class ExampleDealMapping(HubSpotObjectMapping):
    key = "example_deals"
    object_type = "deals"                  # HubSpot's plural: deals, notes, ...; never contacts
    properties = ("dealname", "dealstage")
    contacts = True                        # each record names its associated contact ids
    version = 1

    def apply_record(self, record, *, link):
        if not record.contacts:
            return None
        person = record.person(record.contacts[0])
        if person is None:                 # that contact has not landed yet
            raise ContactNotLanded(record.contacts[0])
        person.statements_for("example").set(
            aspect="example",
            values={"stage": parse_string(record.properties.get("dealstage"))},
            asserted_by=link.asserting_source(),
            link=link,
        )
        return None                        # or the consumer's own row, which the link then holds

    # withdraw_record(self, link) runs once the record is gone at HubSpot; the default withdraws
    # what the link stated. Override it when the mapping keeps more than statements.


# autoconfig.py
SETTINGS = {"ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES.example_deals": "example.deals.mappings.ExampleDealMapping"}
```

Each mapping is one more stream of every HubSpot directory, pull-only, with the
contacts stream's guarantees: a baseline walk, a delta on the type's own
modified property (`hs_lastmodifieddate`; contacts alone use
`lastmodifieddate`) that keeps HubSpot's clock and reaches back for the search
index, equal-time drains, and a daily absence sweep where only HubSpot's word
makes a record gone (a batch read naming it not found, or a merge's survivor
naming it among its `hs_merged_object_ids`, which deals and notes carry as
contacts do). Every read asks HubSpot for the mapping's properties, and, when it
names `contacts`, one association read per page asks for each record's
associated contacts; a record HubSpot names as having none has none, and one it
neither answers nor names fails the read, to be tried again. A directory with no
object mapping reads only contacts. The object streams begin once the directory
has read its contacts in full: on a fresh account the first sync lands the
contacts and the next reads the objects, so a record waits for its contact only
when HubSpot gained that contact since, or does not hold it.

A mapping may project what it keeps. While extracting, `project(record)` receives
the record with its properties as HubSpot sent them and returns the JSON values
to keep; only those and the contact ids are kept on the record's link, in its
history without limit, and compared, so a change the projection hides applies
nothing. The default keeps the requested properties as sent. A notes mapping
that writes each note's full text elsewhere and must not keep it on the link
keeps its digest:

```python
import hashlib

class ExampleNoteMapping(HubSpotObjectMapping):
    key = "example_notes"
    object_type = "notes"
    properties = ("hs_note_body", "hs_timestamp")

    def project(self, record):             # record.properties: as HubSpot sent them
        body = parse_string(record.properties.get("hs_note_body"))
        digest = hashlib.sha256(body.encode()).hexdigest()
        return {"body_sha256": digest, "at": record.properties.get("hs_timestamp")}

    def apply_record(self, record, *, link):
        record.values                      # what project kept: on the link, compared
        record.properties["hs_note_body"]  # the full text, from the read that applies it
        ...
```

`apply_record` receives a `HubSpotObjectRecord`: `id`, `values` (what the
projection kept), `contacts` (associated contact ids, in id order) and
`properties` (the mapping's properties as HubSpot sent them, nullable strings for
the `parse_*` helpers, one the account does not define absent). The backend holds
`properties` in memory from the read to that read's own apply and drops them when
the page is applied; they are never stored on the link or in its history, never
repeated in a refusal and never logged. Every apply the stream driver makes
follows its own read of the record (a page, a rescan of a record that waited, a
sweep, a new baseline after a version change), so they are there.
`record.has_properties` says so; a record applied from what its link holds alone
would have only `values`, and reading `properties` then raises
`PropertiesNotRead`, the driver's missing-dependency refusal: the record waits and
the stream's rescan reads it again, so a mapping never writes from its projection
what it needed from HubSpot. A mapping without `project` reads `properties` as
before. `record.person(contact_id)` is the person that contact
landed on in this directory, or `None` until it has; raising
`ContactNotLanded(contact_id)` makes the record wait and be read again on the
stream's backoff, so it lands once the contact has, whichever stream ran first.
Whatever the mapping states, it states through `link` as the integration
(`link.asserting_source()`). A `ValueError` from `project` or `apply_record`
refuses that record alone, naming the mapping and never the value, while its
page lands; any other error fails the page. A record whose kept values or
contacts changed applies again; a change of a property the mapping does not name,
or one its projection hides, applies nothing. Raising or lowering `version`
replays every record once. When HubSpot says the record is gone, on a read of it
or after two absence sweeps, `withdraw_record(link)` runs. A service key reads
deals with `crm.objects.deals.read`.

A page a mapping makes from a record carries the record's own time as its
creation time: pass the parsed time as `created_at` to the page's creating verb,
`Page.objects.create_in(vault, ..., created_at=at)` or
`RecordBinding.objects.write_sourced_page(..., created_at=at)`, where `at` is the
note's `hs_timestamp` (or HubSpot's `hs_createdate`, when the mapping asks for it).
A later write of the page keeps it.

## Tests

`angee.parties_integrate_hubspot.testing.server` ships `HubSpotServer`, an
in-memory HubSpot account behind `angee.integrate.testing.http.mock_transport`.
Use it with `serve(server)` or the `hubspot_server` fixture, and script contacts,
deals, notes and their associations, merges, deletions, HubSpot's clock (each
answer's `Date`), index lag, refusals and per-record read errors on it. Nothing
calls the real HubSpot API.
