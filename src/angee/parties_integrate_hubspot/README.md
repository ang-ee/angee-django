# HubSpot for parties

`angee.parties_integrate_hubspot` is meant to read HubSpot CRM contacts one way,
from HubSpot into `angee.parties`. It does not do that yet: it registers no
directory backend, connect action or stream. What it ships is the layer such a
backend builds on, and the `hubspot` vendor row.

That layer speaks HubSpot's date-versioned REST API through integrate's shared
HTTP client, with no HubSpot SDK; [the stack guide](../../../docs/stack.md#backend)
records why. `HubSpotClient` takes the authorization headers its caller gives it
and never reads a credential. `HubSpotCursor` holds a stream's position inside a
baseline or delta pass, and `contact_from_properties` maps HubSpot's standard
contact properties onto the parties neutral contact. Their docstrings own the
contracts.

## Tests

`angee.parties_integrate_hubspot.testing.server` ships `HubSpotServer`, an
in-memory HubSpot account behind `angee.integrate.testing.http.mock_transport`.
Use it with `serve(server)` or the `hubspot_server` fixture, and script records,
merges, index lag, refusals and per-record read errors on it. Nothing calls the
real HubSpot API.
