# HubSpot for parties

`angee.parties_integrate_hubspot` reads one HubSpot account's CRM contacts one
way, from HubSpot into `angee.parties`. It registers the `hubspot` directory
backend and the `hubspot` vendor row. A HubSpot directory has one address book
holding every contact. Each contact lands as a person with its names, emails,
phones, employer and postal address, and HubSpot's later edits follow on each
sync. A job title lands only beside a company, because parties keeps a title on
the employment. A person's own edits outrank HubSpot's. A contact HubSpot merges
lands on the person of a record it replaced. A contact HubSpot archives or
deletes is withdrawn after two daily absence sweeps: the first marks its link
unavailable, the second withdraws what it stated. The person stays.

A directory reads with a static-token credential holding a HubSpot service key
that has the `crm.objects.contacts.read` scope. The backend admits no other kind
and no empty token, and connects through no OAuth client. Connection discovery
checks the key and learns the account it reads, so a directory syncs only once
discovery has finished, and its source links open each contact in HubSpot when
HubSpot states the account's web host. Once a directory has learned its account,
discovery refuses a key of another account. The connection test asks HubSpot for
one contact, and it refuses a rejected key and a key without the scope with
separate messages. The addon ships no connect action yet; until it does, a host
creates the credential and a `Directory` with `backend_class="hubspot"` that
attaches it.

The backend speaks HubSpot's date-versioned REST API through integrate's typed
request (`HttpClient.request_model`), with no HubSpot SDK;
[the stack guide](../../../docs/stack.md#backend) records why. Integrate's
`HttpRefusal` classifies every refused request, and `HubSpotError` adds only
HubSpot's own cases: a 423 is transient, and a rate limit without `Retry-After`
waits for the window of the policy HubSpot names. `HubSpotDirectoryBackend` composes the parties directory base,
`HubSpotClient` takes the authorization headers its caller gives it and never
reads a credential, `HubSpotCursor` holds a stream's position inside a baseline
or delta pass, and `contact_from_properties` maps HubSpot's standard contact
properties onto the parties neutral contact. Their docstrings own the contracts.

## Tests

`angee.parties_integrate_hubspot.testing.server` ships `HubSpotServer`, an
in-memory HubSpot account behind `angee.integrate.testing.http.mock_transport`.
Use it with `serve(server)` or the `hubspot_server` fixture, and script records,
merges, deletions, index lag, refusals and per-record read errors on it. Nothing
calls the real HubSpot API.
