# HubSpot for parties

`angee.parties_integrate_hubspot` reads HubSpot CRM records one way, from HubSpot
into `angee.parties`. It calls HubSpot's REST API at the date-versioned
`2026-09` paths through integrate's shared HTTP client. No HubSpot SDK is
involved. It seeds the `hubspot` vendor row and stores no data of its own.

The addon does not register a directory backend yet, so it adds no connect
action and no streams. It ships the pieces that talk to HubSpot and decode
what HubSpot returns:

- **`client.py`**: `HubSpotClient` takes the authorization headers its caller
  gives it and never reads a credential itself. It covers token introspection,
  account details, property definitions and list pages. It walks record ids
  past a given id and searches by modified time. It reads in batches, reporting
  the ids HubSpot did not return, and reads associations, following each
  record's own paging.
- **Errors**: every refusal is a `HubSpotError`. Its message is built from the
  status, HubSpot's error category and the scopes the request needed, never
  from HubSpot's own text. A missing scope names the scope. 401 and 403 are
  permanent. 423, 429 and the gateway statuses are transient, with HubSpot's
  `Retry-After` when sent. Without it, a 429 waits for its rate-limit policy's
  window.
- **Decoding**: HubSpot sends every property value as a nullable string. The
  `decode_*` functions turn one into a string, an exact number, a day (read in
  UTC, never shifted into a local time zone), an instant, a boolean or the
  option values of a multi-select.
- **`backends.py`**: `HubSpotCursor` holds a stream's position inside a
  baseline or delta pass and its modified-time watermark. The stream's own
  phase is passed in. `contact_from_properties` maps the standard contact
  properties (`CONTACT_PROPERTIES`) onto the parties neutral contact.

## Sync model

A baseline walks the list endpoint by record id, which has no result ceiling.
A delta searches on the modified property (`lastmodifieddate` for contacts,
`hs_lastmodifieddate` for companies), ascending, from five minutes behind the
watermark. The overlap is there because HubSpot's search index lags its writes.
A search pages through at most 10,000 results, so a delta query that reaches
that limit restarts from the last modified time it saw. The watermark never
passes the start of the pass that read it.

Search never returns archived records. Deleted and archived records show up as
ids missing from a batch read, or from the id walk. A merge replaces the merged
ids with one new record, whose `hs_merged_object_ids` names them.

## Tests

`angee.parties_integrate_hubspot.testing.server` ships `HubSpotServer`, an
in-memory HubSpot account behind `angee.integrate.testing.http.mock_transport`.
Use it with `serve(server)` or the `hubspot_server` fixture. Records, merges,
search index lag, the search ceiling, association paging and refusals are
scripted. Nothing calls the real HubSpot API.
