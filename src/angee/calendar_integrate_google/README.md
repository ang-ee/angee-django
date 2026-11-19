# Google Calendar

Install `angee.calendar_integrate_google` in the project's `INSTALLED_APPS`. It
adds two Google backends to calendar's [sources](../calendar/README.md#sources)
and no models of its own:

- **Sync in** (`google`). Each of a user's connected Google accounts is one
  source, and every calendar the account reads syncs into a read-only calendar
  on the user's person. Accounts connect through the deployment's `google` OAuth
  client ([registering it](../../../docs/howto/google-oauth-client.md)) with
  `calendar.readonly`, from **Calendar → Connected calendars → Add a calendar →
  Connect Google Calendar**.
- **Publish out** (`google_publishing`). A service-account key is a source that
  publishes local calendars to Google calendars shared with it, Workspace
  resource calendars among them. A published calendar stays local and editable.
  Its events are pushed to Google and never read back, so an edit made in Google
  surfaces as a held conflict the next time Angee writes that event. No screen
  publishes: the key connects and calendars publish through GraphQL.

| Concern | Owner |
|---|---|
| What sync-in maps, and how it pages and keys events | [`GoogleCalendarBackend` and the module docstring](backend.py) |
| What publishing writes, its stamp and its conditional writes | [`GooglePublishingCalendarBackend`](backend.py) |
| Calendar API calls and how Google's refusals read | [`GoogleCalendarClient`](client.py) |
| The two connect verbs and the accounts or keys they admit | [`connect.py`](connect.py), exposed by [`schema.py`](schema.py) |
| Publishing rules, the publishing allowlist and unsubscribing | [calendar's sources](../calendar/README.md#sources) and `CalendarSource` in [calendar's models](../calendar/models.py) |
| The service-account key | integrate's `jwt_bearer` credential ([integrate](../integrate/README.md)) |
| The connect dialog | [`ConnectGoogleCalendarAction`](web/src/ConnectGoogleCalendarAction.tsx), composing integrate's connect-with-account action |

## Checking it against Google

The tests run against a fake Google. This procedure checks a deployment against
the real one, Google Drive mounts included
([their check](../storage_integrate_google_drive/README.md#checking-it-against-google)).

### Set up

Sign in as a platform administrator: OAuth providers, credentials and conflict
resolution are administrator verbs. The deployment's worker runs every sync and
push.

**Google.** Register the deployment's client as the
[how-to](../../../docs/howto/google-oauth-client.md) describes. This check needs:

- the **Google Calendar API** and the **Google Drive API** enabled;
- an audience that admits both accounts the check connects: **Internal** for
  accounts of one Workspace organisation, else **External** with both as test
  users while in **Testing**;
- the Data Access scopes `openid`, `email`, `profile`,
  `https://www.googleapis.com/auth/calendar.readonly` and
  `https://www.googleapis.com/auth/drive.readonly`. Publishing's
  `calendar.events` scope is the key's, never the consent screen's;
- the redirect URI `<console origin>/integrate/oauth/callback`. A local stack's
  console origin is its frontend's `http://localhost` address, port included.

For publishing, in the same Google Cloud project:

1. Create a service account and a JSON key for it. Give it no role and no
   domain-wide delegation: Angee writes as the service account itself and
   refuses a key file that names a delegated `subject`.
2. Choose the target calendar and note its id:
   - a Workspace resource calendar (Admin console → **Directory → Buildings and
     resources**), whose id reads `c_…@resource.calendar.google.com`;
   - or a secondary calendar (Google Calendar → **Settings → Add calendar →
     Create new calendar**), whose id reads `…@group.calendar.google.com` under
     **Integrate calendar**.
3. Share that calendar with the key's `client_email`, with **Make changes to
   events**. A Workspace domain that limits sharing calendars outside the
   organisation must allow it for this calendar: a service account's address is
   outside the domain.

**Angee.**

1. **Integrations → OAuth → OAuth providers → Google** (`/integrate/providers`):
   paste the client id and secret. Its state reads `ready` once the client id is
   set.
2. **Integrations → Credentials → New** (`/integrate/credentials`): kind **JWT
   bearer (signing key)**, the JSON key pasted into **Key file**. The credential is
   yours, and only its owner connects it for publishing.
3. Set `ANGEE_CALENDAR_PUBLISH_TARGETS` to a list holding the target calendar's
   id in the project's settings, and restart the application. Left at `None`, it
   admits every calendar the key can write; anything other than `None` or a list
   of ids fails the `calendar.E001` system check.

### Connect accounts

1. **Calendar → Connected calendars** (`/calendar/sources`) → **Add a calendar →
   Connect Google Calendar → Connect another account**. Google shows its account
   chooser and asks for read-only calendar access, then returns to Connected
   calendars.
2. Connect a second account the same way. Both are listed under **Integrations →
   OAuth → External accounts** (`/integrate/accounts`) and in the connect dialog.
3. Choose one (**Use this account**) and **Connect**: a source named after the
   account is created and its first sync queued. The dialog then lists that
   account as **Already connected**, and it cannot be chosen again.

### Sync in

Before the first sync, give the account in Google a recurring series with one
occurrence moved and one cancelled, an all-day event, an event created in
another time zone, and an event with attendees. After it, **Calendar →
Calendars** and the calendar page show:

- every calendar the account reads, hidden ones included, but none it sees only
  as free/busy;
- the series with its moved and cancelled occurrences, the all-day event on its
  days, and the other zone's event at its own time;
- each event's organizer and attendees (rooms are not attendees);
- events that open read-only.

Then:

- Edit, move and delete events in Google, then **Sync now** on the source: Angee
  follows. A deleted event is cancelled, and a deleted occurrence drops.
- Remove a calendar from the account in Google (unsubscribe from it, or stop
  sharing it). The next sweep finds it absent and the one after withdraws its
  events. Sweeps run at most once per poll interval (15 minutes,
  `GoogleCalendarBackend.defaults`) and **Sync now** does not hasten them. Add it
  back: its events return on the same rows, with no conflict.
- **Unsubscribe** the source: its calendars are archived and their events
  removed. The Google account stays connected, and Google's third-party access
  list still shows the app: nothing is revoked.

### Publish out

Use GraphiQL at `<console origin>/graphql/console/`, signed in, on a deployment
running with `DEBUG` (which turns on `ANGEE_GRAPHQL_IDE`).

1. Create a local calendar (**Calendar → Calendars → New**), then read its id and
   the key's:

   ```graphql
   query { calendars { id name } credentials { id display_name kind } }
   ```

2. Connect the key, and note the source's id. An account's OAuth credential, or
   a key file that names a `subject`, is refused.

   ```graphql
   mutation { connect_google_calendar_publishing(credential_id: "<key id>") { id display_name } }
   ```

3. Publish the calendar to the target:

   ```graphql
   mutation {
     publish_calendar(id: "<source id>", calendar_id: "<calendar id>", remote_id: "<Google calendar id>") { ok message }
   }
   ```

4. Add a single event and a weekly series in the event form. The form edits
   whole events, so move one occurrence and cancel another by inserting their
   overrides; each `original_start` is the start the series gives that occurrence.

   ```graphql
   mutation {
     moved: insert_calendar_events_one(object: {calendar: "<calendar id>", series: "<series id>",
       original_start: "<occurrence start>", title: "Moved", start: "<new start>", end: "<new end>"}) { id }
     cancelled: insert_calendar_events_one(object: {calendar: "<calendar id>", series: "<series id>",
       original_start: "<other start>", title: "Cancelled", start: "<other start>", end: "<other end>",
       status: "cancelled"}) { id }
   }
   ```

5. **Sync now** on the publishing source (or
   `sync_calendar_source(id: "<source id>")`), or wait for its next poll (5 minutes, `GooglePublishingCalendarBackend.defaults`).
   In Google, check the events, the series' recurrence, the moved and the
   cancelled occurrence, and, on each master or single event, the private
   extended property `angee_event` holding its Angee id (`events.get`; its Google
   id is that id in lowercase base32hex, `event_id`).
6. **Conflict.** Edit an event in Google, then the same event in Angee. The next
   push holds a conflict on the source's **Streams** tab. **Keep local** writes
   Angee's version over Google's. **Keep remote** accepts Google's version and
   leaves Angee's event as it is: nothing is pushed until the event changes in
   Angee again, and that push writes over Google's.
7. **Deletes.** Deleting an event deletes it in Google. Deleting an override
   (open the moved occurrence) restores that occurrence in Google. Deleting the
   published calendar is refused until it is unpublished.
8. **Allowlist.** Publishing to a calendar id `ANGEE_CALENDAR_PUBLISH_TARGETS`
   does not list is refused before any request reaches Google.
9. **Unpublish.** **Unsubscribe** the publishing source: the calendar is local
   again with every event kept, the events already in Google stay there, and the
   key is detached, not retired.

### What only Google can confirm

The fake encodes what the code assumes of Google. Watch for these during the
publishing check:

- whether a series' instances carry its `angee_event` stamp
  (`events.instances`); the stamp lookup before an insert counts only masters
  and single events either way;
- whether writing a series changes its exceptions' ETags, which would hold a
  false conflict on the next write of an override;
- whether an event deleted in Google, then in Angee, ends without a held
  conflict;
- whether `calendar.events` alone covers every call, or a permission error shows
  on the **Streams** tab;
- whether Google takes a series' end (`UNTIL` or `COUNT`) as Angee writes its
  rule;
- whether an event Google refuses is set aside while the calendar's other events
  still publish;
- whether all-day occurrences land on their days.

### Where to look

- The calendar pages, and the source's record: its **Last sync** fields and its
  **Streams** tab (open failures, conflicts, links and their history).
- The worker and Django logs.
- Google's [API Explorer](https://developers.google.com/calendar/api/v3/reference):
  `events.list` (`privateExtendedProperty=angee_event=<id>` finds a published
  event), `events.get` and `events.instances`.
