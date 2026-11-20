# Changelog

Notable structural and behavioral changes to the Angee framework and its addons.
The format loosely follows [Keep a Changelog](https://keepachangelog.com).
Releases get version-keyed headings; earlier pre-release entries remain grouped
by the dated effort that landed them, newest first. Day-to-day detail lives in git history — this file
keeps the load-bearing decisions and the deferred follow-ups that outlive the
working plans that produced them. Principles live in `docs/`; concrete contracts
live in code docstrings.

## Unreleased — shared addon contracts, native embeddings and graph retrieval

- `@angee/ui`: `FormView` (and so `Form` and every registered form) takes an
  optional `renderUnavailable(id, unavailable)`: what a saved record's form
  shows when its id reads no record, given the default "Record unavailable"
  state to render when it has nothing else; omitted, the default shows as
  before. `angee.parties`: the person's and organization's own pages pass it,
  so a direct link to a merged-away person or organization opens the survivor's
  page for someone who reads both records (through `party_record`, keeping the
  app they are in) and shows "Record unavailable" to anyone else. Debt:
  following a merged-away record to its survivor (`merged_into`) is the shared
  merge owner's fact, used beyond parties, yet `party_record` keeps it under
  parties. Its fix is a generic survivor-of-an-id root beside `merge_records`
  (`src/angee/graphql/merge.py`) with the same authorization, which the shared
  record form asks whenever a resource its metadata marks `mergeable` misses, so
  no form passes a hook and parties keeps only its concrete page
  (`party_record`'s `concrete_kind`).
- `angee.parties`: one verb makes the person of a contact an integration's
  record carried (a name, an email address, a phone number),
  `Party.objects.claimant_as_system`. Each source creates its own people: the
  caller hands over the record that carried the contact (`evidence`) and its
  rule of which records are its own, as a queryset of them (`own`: the record
  links of its integration). The email decides, and a contact without one is
  decided by its number: the person found again is one whose standing claim on
  a handle of that address has a row of `own` behind it, never a person
  another source, a user or a person made, whoever holds or claims the
  address. A trashed person is nobody's answer, a merged one is found as its
  survivor, of several the earliest. An email that cannot be an address at
  all is no address and a number the handle owner reads nothing in, or
  refuses, is no number: each is left out and makes no handle, so two contacts
  that gave the same non-address are never one person for it. What can be an
  email address is one predicate on the handle owner,
  `Handle.is_email_address`: exactly one `@`, a non-empty name part with no
  whitespace or control character in any script, a domain that passes the
  domain rule of Django's `EmailValidator`, and no longer than an address may
  be or the handle's value holds. What the person does not hold
  is recorded as a claim for review with the record behind it
  (`propose_claimed_handle_as_system`) and moves nothing; a person found again
  has nothing new stated of them and carries a handle only when they hold it
  or a record of the caller stands behind their claim on it. The rows of the
  address and of the number are locked in one call before any claim is read,
  and which claims are the caller's own is one read of its records by key, in
  batches within the database's parameter budget. The verb proposes no pair: a
  claim links its person to the address, so the pairing job proposes them.
  Which claims still stand is one scope, `PartyStatementQuerySet.standing()`
  (each party's strongest current stance on a handle, where it asserts a
  value), which `people_for_emails` and the party graph's identity lens now
  read instead of spelling the rule. The read behind the verb,
  `standing_claims()`, adds to each standing claim the records its evidence
  names, those of a claim a merge superseded under it included: two sources'
  claimants of one address, once merged, are each found as the survivor, where
  a read of current stances alone had one of the two sources make a new person
  after every merge. No migration, resource or permission change. Limits: two
  callers that meet two spellings of one new address at once each make its
  row, and so its person, which the pairing job then proposes; a number that
  is no valid international number is read as its digits.
- `angee.parties`: a merged-away party is no longer a row. The `people`,
  `organizations` and `parties` resources read `canonical()` parties, as
  handles, addresses and relationships already did, so a party merged into
  another (or trashed) leaves the lists, their counts and groups, the
  repeated-values groups and the pickers, and the People list's pager agrees
  with the workbench's "All people" count. The new `party_record(id)` root
  resolves a party id, a merged-away one included, to the live party it names
  now; the record redirect (`/parties/records/<id>`, where references to a
  party link) and the Party form read it, so such a link to a merged-away
  record opens its survivor. Fixes the merged record appearing beside its
  survivor (and the batch merge refusing that group). Limits: a party that was
  trashed but never merged leaves the lists and cannot be found in the
  interface to restore until the framework has a trash surface (the shared
  `restore_record` verb still restores it by its id); a party that every one
  of its sources has withdrawn leaves the lists too.
- `angee.parties`: a list's Merge reads the list's grouping
  (`collection.selectedGroups`). One ticked record opens "Merge into…" and two
  open the compare page, whatever the grouping. Three or more on an ungrouped
  list are one set; on a grouped list, one set per group of two or more ticked
  records with a value, never one set across groups, each headed by its group and
  marking a member that no longer matches it; a group of one or without a value is
  left out and the batch dialog says how many. A grouped list that cannot place a
  ticked record offers no batch, and neither does a grouping that yields no set:
  the verb is disabled with its reason. A batch takes the first 50 sets in the
  list's order and the dialog says how many remain.

- Grouped lists find repeated values. People group by `display_name`,
  `given_name` and `family_name`, Organizations by `display_name` and
  `legal_name`, beside their existing axes (`people_groups` and
  `organizations_groups` gain those group-by values). A group level's
  `repeated` (`GroupSpec`, "Repeated values only", `?group=display_name:repeated`)
  makes the server group read keep only groups of more than one record the actor
  reads (`having: {count_gt: 1}`) and leave out the records with no value (null,
  and blank text), so counts and group pages are the repeated groups'. The switch
  is offered per level, where the level's axis at its granularity can tell its
  empty group apart. A view switch or a URL that leaves the flag on a view grouping
  without the native server read (client row model, authored `source`,
  `RowsListView`, a gantt or contributed kind) drops it and keeps the grouping;
  only an authored default asking for it is refused. A repeated grouping shows no
  footer totals. `ResourceQuery.toGroupRead` assembles a level's read;
  `groupAxisId` and `groupSpecsFrom` (`@angee/metadata`) own a level's axis
  identity and the stack decoder. A page's `bulkActions` receives
  `collection.selectedGroups`, the only read of the split: absent on an ungrouped
  list, `null` when a selected row cannot be placed (selected without its group,
  shown now under another group or several, missing from a page that listed it or
  its groups and shows again, or loading; or on a surface that places no rows:
  browser grouping, lane boards), else the selection split by its innermost groups
  with their parents; a hint the server never trusts.
  `Button` and `SelectionBarAction` take a `disabledReason`: the verb's tooltip
  and accessible description, focusable while disabled, dimmed through
  `data-disabled`, without an `href` on a link it renders, and one element tree as
  the reason comes and goes; an empty reason is no reason (`PaneRow` too). The
  record action bar's band verbs compose it.

- `angee.parties`: one owner proposes same-person pairs. A pull-only
  directory's contact that may only claim an address another live party is
  linked to proposes them through `Party.objects.propose_same_person_as_system`
  (reason `parties.directory.contact_claim`), citing the handle that links them,
  once the page commits, instead of through the directory's own rule. The
  `parties.directory_match` rule stays for what no address links, which the
  verb cannot cite and its job would withdraw: the people of the records a merge
  at the source replaced, and an account whose sign-in address no handle of
  theirs carries. Its earlier proposals of address pairs stay in review as they
  are, the pairing job states no second proposal beside one, and merging the two
  withdraws it. A contact's phone number another live party is linked to is now
  only claimed, like an email (`PartyManager._contested_addresses`, through the
  pairing's read `same_person_links(addresses=, every_address=True)`): before, it
  was stated in full and its holder moved to the contact's person. A number four
  or more parties are linked to is still only claimed and pairs nobody. Changes
  in what is proposed: a pair of a person later trashed or merged leaves the
  review at once and is withdrawn by the next pairing run; two records of one
  directory that share an address, and a party linked only by a name
  suggester's claim, are no longer proposed. Upgrade: no migration, resource or
  permission change. A number a directory already took this way keeps its holder
  until a person merges or decides the pair, or the number is restated: an
  unchanged record is not applied again, and a changed one finds the contact's
  person holding the number and states it in full again.

- `angee.parties`: the read of a directory's contacts is a stored grant alone.
  `parties/directory#contact_read` is `contact_reader` (revision 31); the
  integration owner's column arm is gone, because a column arm is never folded
  away by an actor's grant profile and kept the chain from a party to a
  directory in every read of a party, for every actor (a member's person
  document planned about 17.9 MiB more). `Directory.objects.connect` grants the
  connecting owner `contact_reader` (`Directory.grant_owner_contact_read`, the
  one writer), and provision grants each existing directory's owner where
  missing (`grant_directory_owner_contact_reads`, beside
  `resolve_stale_owners`). A transfer moves no grant: a person grants a later
  owner. Upgrade: `rebac sync`; provision runs the repair.

- `angee.messaging`, `angee.workflows_integrate`: a send and a workflow step ask
  about discovery again once they hold their lock. Both asked before taking
  `delivery_lock()` or `sync_lock()`, so a secret re-entered while they waited
  for it sent one reply, or ran one step's IO, on the new secret. Under the lock
  they now ask the channel or bridge to re-read its own readiness columns
  (`Integration.require_stored_discovery_ready`): a send defers as for a pending
  discovery, and a step waits as for contention.

- `angee.integrate`: the discovery checks cost no more than they need. A push
  asked about discovery for each candidate before its pinned, held, unchanged and
  released skips, each time reading the whole integration row; it now asks once
  per write, after the skips, reading only the columns
  `Integration.require_discovery_ready` uses (`Integration.discovery_readiness_fields`),
  and every stream lock (`SyncStreamManager.lock_current`) joins only those
  columns of its integration instead of the whole row. The exemption from the stream lock's discovery check is a module-level context,
  `angee.integrate.states.exempt_from_discovery()`, instead of a stream manager
  method that ignored its manager, so pinning a record and dismissing a failure
  no longer read the stream row only to reach it.

- `angee.integrate`: re-entering exactly what is stored changes nothing, even
  beside a transient answer. `CredentialManager.reenter_secret` compared the
  whole decrypted material, so the identical secret re-entered while a pairing
  answer was stored (`Credential.update_material`) sent the integrations to
  discover again. It now compares the kind and its own secret fields
  (`CredentialKindHandler.secret_material_fields`), and holds only a keyed
  digest of them across the caller's write, not the decrypted material in the
  generator's frame, which an error reporter keeps.

- `angee.integrate`: a disconnected integration waits for the discovery its
  reconnect runs. A secret re-entered on a disconnected integration leaves
  discovery unsettled until `connect` awaits it, but
  `Integration.require_discovery_ready` took that for a discovery that failed
  and refused permanently, so a queued reply failed for good. It now refuses
  transiently ("Connection discovery runs when this integration is connected
  again."), and delivery defers the send as it does for a pending discovery.

- `angee.integrate`: one rule says which credential is derived. Once a source
  grant was removed by a path that skips its disconnect, `Integration.derived_credential_id`
  still called the attached credential derived while the manager's own query did
  not: `CredentialManager.store_derived` refused an overwrite its contract allows,
  and `reenter_secret` accepted re-entry of that credential into a discovery that
  could only fail for want of its grant. The query is gone; the re-entry refusal
  reads the property on the locked rows the credential serves, and `store_derived`
  on the rows it serves. The refusal now also says to connect the integration
  again when the grant is gone.

- `angee.knowledge_integrate_notion`: a token of another workspace fails
  discovery. Notion's discovery called `GET /v1/users/me` and recorded nothing,
  so an internal token of another workspace, re-entered on a connection, passed
  rediscovery, and the sync resumed on the old cursor and links and mixed two
  workspaces into one vault. Discovery now reads the workspace the token's bot
  belongs to (`bot.workspace_id`, which Notion's answer requires), and
  `NotionBackend.apply_discovery` keeps the first one in
  `Bridge.subscription_state`. Another workspace fails discovery with "This
  connection belongs to another Notion workspace; create a separate connection.",
  and an answer that names no workspace is refused rather than matched. A
  connection discovered before it kept a workspace is checked against the
  workspace its Notion account was authorized for (the OAuth token answer that
  connect keeps in `ExternalAccount.identity_claims`); only one with no such
  account, made with an API token, records the workspace its next discovery reads.

- A deployment declares its static page head in `ANGEE_UI.shell.site` (`title`,
  `description`, `icon`, `touchIcon`, `image`), selecting files addons name in
  their `web/package.json` `angee.assets`; Vite emits them and writes the title,
  description, Open Graph, `twitter:card` and icon tags. Behaviour change: a
  deployment with `shell.brand` now gets the brand's name as its static
  `<title>`, and nothing else without `shell.site`. Absolute preview URLs use the
  stack's `ANGEE_PUBLIC_ORIGIN` environment variable, the only origin the web
  build reads: one authored only in a settings file leaves `og:url` out and
  `og:image` relative.

- Addons require django-zed-rebac 0.36.1. With none of its new settings set,
  statements, statement counts and decisions are 0.35.2's, so nothing changes for a
  host. (0.36.0 cannot be imported before app population, which Angee's pytest
  plugins and permission modules need; 0.36.1 restores it, and the floor keeps hosts
  off 0.36.0.) It adds `REBAC_ENABLED` (default `True`): a private single-user
  project may set `REBAC_ENABLED: false` in `settings.yaml` to enforce nothing,
  which is also the baseline for profiling a document's own queries without REBAC.
  The library still stores relationships and syncs the policy, so `rebac sync` and
  provisioning are unchanged, and it emits the system check `rebac.W011` and one
  startup warning while unenforced. Its experimental read settings stay unset here.

- Parties: two people selected in the people list (or two organizations in theirs)
  offer Merge… in the selection bar, which opens the existing compare-and-merge
  page for that pair; one, or more than two, show it disabled with the reason. The
  merge page's Cancel and Keep separate now go back to where the comparison was
  opened (a list, a person, the review queue, in whichever app mounts the list)
  instead of always to the review queue, which stays the fallback for a
  comparison opened directly.

- New addon `angee.parties_integrate_hubspot` (`angee-parties-integrate-hubspot`)
  registers the `hubspot` directory backend: one HubSpot account's contacts read
  one way into parties over HubSpot's `2026-09` REST paths, with no HubSpot SDK.
  Its requests are integrate's pinned request (`HttpClient.request`), sent
  through the backend's own client (`HubSpotClient(http, headers)`), whose
  refusal rule (`HttpRefusal`) it shares; it adds a 423 as transient (at least two seconds), a
  429 without `Retry-After` waits for its policy's window (ten seconds, or an
  hour for the daily limit, which its message names), and HubSpot's rejection of
  a key that lacks a service key's usual shape says to check it for extra or
  missing characters. Its `credential_blocker` admits only a static-token
  credential holding a service key with `crm.objects.contacts.read` that does not
  need reconnecting, judging the key as the request sends it (only an empty one
  is refused), and it declares no OAuth client. Connection discovery
  (`requires_connection_discovery`) learns the account's hub id, and its web host
  when HubSpot states one, into the directory's `subscription_state`, so a
  directory syncs only after discovery and its source links open each contact in
  HubSpot; a later discovery that finds another account refuses the key. The
  connection test keeps the base's check, refusing a credential that needs
  reconnecting without a request, then is one one-contact read. A baseline lists contacts by record id;
  a delta searches by modified time without an offset, reaching back five minutes
  for HubSpot's lagging search index only until a pass that started that long
  after the newest change read has completed, so a quiet account costs one search
  per poll; every pass keeps HubSpot's time (the `Date` of its first answer),
  never the local clock; an unreadable stored cursor
  starts a new baseline, and a transient refusal inside one page keeps what that
  page read. A merge's survivor lands on a replaced record's person. A contact
  that leaves HubSpot's id walk (archived, deleted or merged away) is withdrawn by
  the second absence sweep; a read of the contact withdraws it at once only when
  HubSpot names it not found or a survivor names it replaced, and an id HubSpot
  merely leaves unanswered is a transient refusal; an empty id walk is believed
  only when HubSpot's search counts no contact either. Each record of an answer is
  validated alone: one HubSpot states unreadably is quarantined under its id
  (`unreadable_record`) while its page lands, and one without a usable id is
  logged and skipped. A platform admin connects an account
  from the console: `@angee/parties-integrate-hubspot` adds a "HubSpot account"
  entry to `parties.directories#toolbar`, whose dialog sends a name and the
  service key (masked, trimmed) to `connect_hubspot_directory(name, service_key)`, which runs
  `Directory.objects.connect`; discovery then arms the first sync. The `full`
  project profile enables the addon. A consumer's `DirectoryMapping` of the
  `hubspot` backend names contact properties by their internal names: while one
  does, every read of contacts asks HubSpot for them beside the backend's own and
  hands their values to the base, and a property the account does not define,
  which HubSpot leaves out, reaches the mapping as not sent. `parse_string`,
  `parse_options`, `parse_number`, `parse_bool`, `parse_datetime` and
  `parse_date` (`angee.parties_integrate_hubspot.client`) read HubSpot's string
  values by property type for mapping authors; each raises `ValueError` for a
  value not of its type, which refuses that contact alone. A consumer reads
  another HubSpot object type (deals, notes) as plain records with a
  `HubSpotObjectMapping` registered under
  `ANGEE_HUBSPOT_OBJECT_MAPPING_CLASSES.<key>` and checked at startup: each adds
  one pull-only stream (`objects`, partitioned by the key) with the contacts
  stream's pass, sweep and per-record refusals, on the type's own modified
  property (`hs_lastmodifieddate`), asking for exactly the mapping's properties
  and, when it names `contacts`, each record's associated contacts in one
  association read per page (`HubSpotClient.associations`). Its `apply_record`
  receives a `HubSpotObjectRecord` (id, properties as sent, contact ids, and
  `person(contact_id)`) and states through the record's link as the integration;
  `ContactNotLanded` makes a record wait for its contact on the driver's
  missing-dependency refusal; `withdraw_record` runs when HubSpot says the record
  is gone; its `version` is the stream's mapping, so a change replays every record
  once. An optional `project(record)` turns the record as HubSpot sent it into the
  values the link keeps and compares (by default the requested properties as
  sent), so a note's body can be kept as its digest and a change the projection
  hides applies nothing; `apply_record` receives those `values` and, held in
  memory from the read that applies it and never stored or logged, the
  `properties` HubSpot sent, so it can write the full text. Without them (an apply
  that did not follow its own read, which no driver path makes today), reading
  `properties` raises `PropertiesNotRead`, a missing-dependency refusal the rescan
  answers with a new read. An association read
  takes an id HubSpot names in its `NO_ASSOCIATIONS_FOUND` error as having no
  contacts, and refuses any other error or an id neither answered nor named, for a
  later retry. A directory declares its object streams only once it has read its
  contacts in full, so a first sync lands the contacts and the next the objects,
  and no record waits for a contact HubSpot already held. With no object mapping a
  directory reads only contacts. Operator messages
  never repeat HubSpot's message text or error category. The scripted account
  (`angee.parties_integrate_hubspot.testing.server`) builds its invented keys at
  runtime (`service_key`), so no literal in the repository has a real key's shape.

- `angee.parties`' web fragment owns the connect ceremony of a contacts
  directory: `ConnectDirectoryAction` (`@angee/parties`) renders the menu trigger
  and the typed mutation dialog with the shared submit and failure wording, and a
  vendor declares only its connect mutation, its fields, its message range in the
  parties namespace (`<prefix>.button`, `.title` and `.description`) and how raw
  values become the mutation's variables. CardDAV and HubSpot are reduced to those
  declarations: their copied dialogs and their own i18n namespaces are deleted,
  and their words join the parties namespace under `directory.carddav` and
  `directory.hubspot`. A connect now refreshes the directories list, which is a
  resource view (`useAuthoredResourceMutation`; before, only the live
  subscription updated it), and its entered key or password leaves the mutation
  cache once the connect settles (`transient`, whose `@angee/refine`
  documentation now names secret inputs). The fragment exports the toolbar as
  `DIRECTORIES_TOOLBAR`, and each connect entry `requires` `DIRECTORY_CREATE`
  (`parties.Directory#create`, the permission `Directory.objects.connect`
  checks), so the Connect menu is absent for an identity the mutation refuses.

- `@angee/ui`: the `password` widget is the masked entry of a secret that is not
  the person's own sign-in password, and tells the browser so
  (`autocomplete="new-password"` when the field declares no hint of its own):
  a vendor key after a name field no longer
  reads as a login form the browser fills with the console's saved sign-in. The
  sign-in form keeps its own `current-password` hint. The widget's read form
  shows nothing for an empty value instead of a mask. The `textarea` widget shows
  the field's placeholder, and its label only when the field declares none.

- `angee.integrate`'s console credential form enters a static token's key in the
  `password` widget (it was a plain text input) and drops the entered material
  from the mutation cache once the create settles (`transient`). A private key
  and a service-account key file are multi-line, so they stay in text areas,
  which now show their placeholders.

- `angee.knowledge`: a source's text about a person, as a note in their folder:
  `RecordBindingManager.write_sourced_page(party, vault=, role=, folder_title=,
  title=, body=, written=)` finds the note by its binding to the party under
  `role` (a slug of the consumer's; knowledge's `related` and `folder` are
  refused), the oldest live one, so a renamed or moved note stays the source's,
  and makes one titled `title` under the folder only when there is none (a
  taken title refuses it, saying so). It writes nothing for the same text, replaces a note
  that still holds what the source last wrote (compare-and-set on its hash),
  and appends the newer text, quoted under a marker line, below a person's
  edit, never over it (`SourcedWrite`). `angee.parties`:
  `DirectoryMapping.applied(link)` returns what the mapping's projection was
  when the link last applied, which a mapping passes as `written`.
- `angee.knowledge`: one vault for people. Knowledge's install tier provisions
  `Vault.PEOPLE` (`knowledge.vault_people`), unowned and administered by
  platform admins and knowledge's vault roles; a deployment grants it with a
  vault's ordinary grants (viewers through its share surface, editors
  administratively). Whoever reads it reads every person's pages, people they
  cannot read in parties included; finer control is a later version.
  `RecordBindingManager.party_folder(party, vault=, title=)` finds or makes a
  party's folder page bound under the new `FOLDER_ROLE` (`folder`), locking the
  party first, and returns the oldest when a merge left several.
  `PageManager.create_in` without an actor, inside a system context, creates
  the page as system work. Nothing changes in knowledge's permission schema.
  Upgrade: load the install tier (provision does).
- `angee.parties`: a directory mapping's refinement may set the preferred flag
  of an email or phone the source sent (`REFINABLE_FIELDS` gains `emails` and
  `phones`, `POINT_FIELDS`); a refinement changing an address's value, label or
  order fails the extract. The mark on the contact's own address is then stated
  by the ingest once and goes with the mapping; a handle the contact does not
  bring keeps `Handle.objects.upsert(..., is_preferred=True)`.
- `angee.parties`: a person's nickname is identity, like the name parts:
  whoever reads the person reads it, a directory's contact readers, identity
  readers and thread or task readers included. `parties/person` drops
  `read__nickname` (revision 28), and `PersonType.nickname` is a non-null
  `String`. The notes, the first-met note, the handle count, the birthday and
  the anniversary stay private (`read_private`). Upgrade: `rebac sync`; clients
  regenerate the nickname's type.
- `angee.parties`: a directory mapping refines the neutral contact before the
  ingest states it (`DirectoryMapping.refine` and `refines`, among
  `REFINABLE_FIELDS`: the name parts, the dates, the employment), so a source
  field that belongs in a group the ingest states, a house's name for a person
  as the `nickname`, is compared, versioned and stated by the ingest's own
  statement, and goes with it when the mapping is removed. A mapping may declare
  no statement group: one that only refines, or writes tags
  (`TagAssignment.objects.attach`) or a handle's preferred mark
  (`Handle.objects.upsert(..., is_preferred=True)`) in its `apply_record`, which
  now writes nothing by default. Tags and marks are not taken back when the
  mapping or the link goes; the ingest keeps an address the contact itself lands
  as preferred as its source says. `parties.E003` refuses only a group the
  person has not; `parties.E005` a refined field outside `REFINABLE_FIELDS`;
  `parties.E006` two mappings of one backend refining one field.
- `angee.knowledge`: a party (a person or an organization) is a record-binding
  target, read through the party's `read` and written through its `write`.
  Parties cannot name knowledge (knowledge depends on messaging, which depends
  on parties), so knowledge declares the arm in its own
  `permissions.extends.zed` (revision 4) and now requires `angee-parties`
  directly; a person's and an organization's records list
  `knowledge.RecordBinding` and show knowledge's Pages group. A merge moves the
  merged party's bindings to the survivor, collapsing one page bound to both
  under one role. Upgrade: `rebac sync`.
- `angee.parties`: a person has `languages`, the ISO 639 codes they use with
  us, most preferred first (`LanguageCodesField`: a JSON list that stores a
  two-letter code where one exists and refuses a region subtag), and
  `occupation`, free text apart from an employment's title. Each is a statement
  group of its own on the person, ranked person, system of record, directory,
  import; no neutral contact carries them (`Person.MAPPED_ASPECTS`), so
  `Person.contact_aspects()` leaves them out and a directory mapping states them.
  The people resource reads and writes both and the person form shows them.
  Upgrade: the host generates the parties migration (two person columns, the
  same two on the person statement table, its aspect choices and per-group
  constraints) and applies it.
- `angee.parties`: each source creates its own people, and a merge joins them;
  adoption is an option of a pull-only directory, off by default. A directory's
  configuration has a model of its own, `DirectoryConfig`
  (`angee.parties.backends`, the base backend's `config_model`, which a
  backend's own model extends, as `CardDavConfig` now does), whose
  `adopt_existing_people` turns adoption on; every save and
  `Directory.objects.connect` validate it, and a directory that writes back
  refuses it. Off, a contact lands on the person its own link names (through a
  tombstone too), on the person a replaced record of its stream hands over, or on
  a new person, which only claims an address someone else holds, claims or signs
  in with and proposes that person as its same person;
  `PartyManager.ingest_contact` refuses any other person a caller hands it. On,
  adoption works as before, with every limit and `parties/person#adopt`, and its
  known limit (an adopted person's values no statement holds) stands.
  `DirectoryBackend.adopts_existing_people()` is the one predicate, asked by the
  target decision before it enters adoption. Upgrade: an existing pull-only
  directory adopts nobody until its configuration turns the option on; a link
  already bound to an adopted person keeps it. A vendor backend implements
  nothing new; one that declares its own config model extends `DirectoryConfig`
  to offer the option. The permission schema is unchanged (revision 27).

- `angee.parties`: taking control of an address never refuses.
  `HandleManager.claim_control`, which OIDC login, `Directory.objects.connect`
  and the attribution of a user's own messages (`HandleManager.for_user`) call,
  states the address for the user's person as a claim for review at
  `Party.CLAIM_CONFIDENCE` where someone holds it whom the user may not take it
  from (`PartyManager.holding_blocker`), and leaves control and the holding as
  they are. A login whose provider address another party holds signs in and
  logs no error, and a connect whose account address another party holds
  connects; both raised the holding rule's refusal since it moved to the handle's
  resolution. Neither contested path (another controller, another holder)
  rewrites the handle's display fields any more. The holding rule itself still
  refuses a person's winning stance.

- `angee.parties`, `angee.parties_integrate_carddav`, `angee.iam`: smaller
  findings of the third adoption review. A directory's configuration is
  validated strictly where a person edits it, `Directory.objects.connect`
  (each vendor's connect mutation hands it its configuration): a setting the
  backend's config model does not know is a `ValidationError` naming it, before
  the connection test. A save that edits nothing keeps what is stored, so
  CardDAV's configuration (`CardDavConfig`, `CardDavStreams`, `ContactsPolicy`)
  keeps unknown keys at every level instead of dropping them on every save, and
  its reader ignores them. `connect` resolves the backend once, before anything
  else, and checks its vendor row before building, so an unknown backend is
  refused as itself, never inside the handling of another error. An
  organization link returning from a tombstone keeps its own organization
  (`ingest_organization` refused it when handed it, and minted a second when
  handed none). `UserManager.sign_in_addresses(emails, *, key=None)` also
  compares by a caller's key, read among the person accounts at the given
  addresses' domains in the same one query; parties passes the handle's
  normalized key, so an account address spelled the provider's other way, which
  neither the contact nor any handle uses, still counts. `Party.CLAIM_CONFIDENCE`
  names the confidence of a claim for review (0.4), and a test holds the
  schema's `stating_directory` threshold to `Party.REVIEW_CONFIDENCE`.

- `angee.parties`: the directory mapping seam's loose ends. A mapping refuses a
  record by raising `ValueError` only: a `TypeError` (or any other error) from
  `project` or `apply_record` is a bug and fails the page with its traceback,
  where it refused every record with no trace. A refusal is logged as a warning
  with the mapping's key and the record's key, never the value. A refused record
  is retried by the stream's rescan on its backoff, so a mapping fixed in code
  applies it without a source change (it was documented as applying once its
  source changes). A mapping may not declare a group the contact ingest states
  (`parties.E004`, which would overwrite the ingest's statement); a mapping that
  states a group no registered mapping declares fails the page with
  `ImproperlyConfigured` (it used to be stated and withdrawn at once). The
  ingest's groups are read from parties' own declarations,
  `Person.contact_aspects()`, which replaces the hand-kept
  `PartyManager.contact_aspects`; the base withdraws the groups nobody states
  before the mappings apply.

- `angee.parties`: locks and stale reads. `DirectoryBackend.prepare_page`'s one
  lock call also takes the people its tombstones' links name and the person of
  an account a page address signs in with (the new
  `PartyManager.account_people(emails)`, the account rule `people_for_emails`
  already answered by), with their handles, which a tombstone's withdrawal and a
  proposal with such a person used to lock after the page's parties. A party
  merge locks every participant's handles, then the participants, in one call
  (`Party.lock_merge_records`, the base's own hook; `Party.lock_for_merge`, which
  locked one participant's handles after another participant, is deleted). A
  tombstone's withdrawal marks the addresses it stated changed in the page's
  lookup (the new `PartyManager.withdraw_contact`), so a later contact of the
  page with that address no longer claims an address nobody holds. The
  PostgreSQL lane runs the directory backend's lock tests by node (a page
  against mail ingestion, a merge, a confirmation and an account's adoption);
  the module's strict expected failure (open decision O15) would read as a skip
  in that lane's zero-skip gate.

- `angee.parties`: the confirm rule is keyed on the address, at the owner of the
  effect. Every person's stance that wins a handle for a party (a confirmation,
  taking control with `HandleManager.claim_control`, a confirmation a history
  revert restores) is checked in the handle's resolution (`Party.apply_handle`),
  where its author needs write on every other holder of the address as the page
  lookup reads them (the new `PartyManager.holding_blocker(party, handle, *,
  author)`): each live party any spelling of the address resolves to, an
  organization included, each lapsed holder, and the person of the account whose
  sign-in address it is. An account takes its own sign-in address whoever holds
  it, and nobody may while the account has no person. A refusal raises
  `PermissionDenied("This address cannot be given to this party.")` whoever the
  holder, and the whole write rolls back. The `Party.confirm_statement` override,
  which read only the exact handle row's live holder, is deleted; a merge's own
  maintenance is admitted.

- `angee.parties`: a lapsed holder is still the holder. A trashed person whose
  applied statement of an address is still current (the new
  `PartyManager.lapsed_holders(platform, values)`) keeps the address for what a
  pull-only contact may state: the contact only claims it, and a claim names
  nobody to adopt by it, so trashing the holder before the contact first states
  the address, while the source dropped it, or while another source only claims
  it no longer hands the address to the directory's person for good once the
  holder is restored. `EmailLookup.lapsed` carries them;
  `PartyManager.people_for_emails` answers exactly as before. A contact link
  leaving a person reads the addresses it withdrew again before the contact's
  are judged.

- `angee.parties`: every adoption limit is checked on rows read under the page's
  locks, for every way a link reaches a person. Only a bound, observing link of
  the contact's own stream keeps its person or hands a replaced record's over
  (`PartyManager.holding_links`); any other link's person, a pull-only record
  returning from a tombstone included, is admitted again, and a two-way stream's
  own link keeps its person through a tombstone. "No other contact of the
  directory names the person" counts every bound link that has not withdrawn it,
  tombstoned ones included (the records a merge replaced are the contact's own).
  `PartyManager.adoption_blocker(person, *, link, parsed, lookup=None,
  replaces=())` replaces `through=` with the contact `parsed`: it refuses when any
  address the contact carries, in any spelling a handle holds, is a person
  account's sign-in address, whether or not the account has a person, and it
  reads the person's liveness (neither trashed nor merged away) and account on a
  freshly loaded row. A pull-only contact also only claims an address an account
  signs in with. `DirectoryBackend.prepare_page` locks the page's whole handle set
  (every spelling of its addresses and every handle stated on the parties its
  links and addresses name) and those parties in one call, then reads who its
  addresses name once (`PartyManager.lookup_emails`, returning the new
  `EmailLookup`); the target decision and `ingest_contact(..., lookup=)` read it,
  an address a record lands is read again by the next record that asks, and each
  adoption verdict is kept for the page. `ingest_contact` takes the contact's
  locks before it decides, whatever its target. `angee.iam`:
  `UserManager.sign_in_addresses(emails)` answers which given addresses a person
  account signs in with, by the key `person_for_email` reads, in one query.

- `angee.parties`: smaller findings of the second adoption review.
  `Directory.objects.connect` refuses to run inside a caller's transaction
  (`RuntimeError`: its connection test is remote); only a missing vendor row reads
  as "load resources", and any other `ImproperlyConfigured` stays itself; each
  directory's credential is named `<backend label> — <display name> (<directory
  id>)`, unique by construction, so a second connection or a reconnect of one name
  gets its own credential, and the refusal of a taken name is gone (it counted
  disconnected and trashed integrations too). The directory is created
  disconnected and then connected with its credential, entering connection
  discovery, and the first sync is requested only when the directory is ready for
  work: a backend that requires discovery gets its first sync when discovery
  completes, where connect used to log a refused request with a traceback. CardDAV's
  configuration ignores unknown keys on every save and at read and validates the
  keys it knows, where any full save of a directory carrying an unknown key was
  refused. `parties/person#adopt` follows editors where they are granted, on the
  party (`owner + editor + party->editor`): the person-level arm alone was empty
  in practice. Schema revision 27: `rebac sync` after compose. A contact link
  leaving a trashed person withdraws what it stated there even while another link
  of its source names that person (`RecordLink.withdraw_statements` would move it
  to that link), and the handle an adoption found the person by is read only from
  the source's current asserted identity statement.

- `angee.parties`: organizations take no existing target yet.
  `PartyManager.ingest_organization` takes the record's locks (the passed
  organization and every spelling of its handles) before it decides, and refuses
  an existing organization new to its link with a `ValidationError`, where it
  used to bind any organization it was handed, unadmitted, before locking, and
  state its addresses in full there. A link keeps its own organization, and an
  unbound one mints. Matching an existing organization, and its admission rule,
  come with the organizations stream (open decision O9). No caller outside the
  tests passes one today; no vendor stream lands organizations yet.

- `angee.parties`: a claim stays a claim. An address a pull-only directory
  already claims on its contact's person is claimed again on every record until
  that person holds it through another statement or a person confirms the claim;
  before, a holder that lapsed (trashed, or its statement withdrawn) left the
  contact's claim as the only one, the next record stated it in full, and the
  directory then outranked the returning holder's rule statement for good.
  Confirming a handle statement another live party holds now needs write on that
  holder too (`Party.confirm_statement`, so the review queue's accept and the
  identity review's confirm both check it); it used to need write on the claimant
  only. A claim for review lends a directory's contact readers nothing:
  `parties/handle#stating_directory` reads current claims at confidence 0.5
  (`Party.REVIEW_CONFIDENCE`) or above, so the holder's handle row, its display
  name and its controlling account stay with their own gates. Schema revision 26:
  `rebac sync` after compose.

- `angee.parties`: a consumer addon maps a pull-only directory backend's source
  fields onto its own person columns with a `DirectoryMapping`
  (`angee.parties.backends`), registered under its key in the new
  `ANGEE_DIRECTORY_MAPPING_CLASSES` setting from its `autoconfig.py`. It names
  the backend, the source `fields` and a `version`; `project(row)` keeps what may
  enter the system and `apply_record(person, values, *, link)` states it on the
  landed person through the contact's link, after the ingest in its savepoint.
  A backend asks its source for `DirectoryBackend.mapped_fields()` and passes
  their values to `observe(..., row=...)`; the payload and the compared mapped
  payload keep only the projections, under a `mappings` key that appears only
  while a mapping applies. Each mapping is one `MappingContribution` of the
  contacts stream, so adding, removing, exchanging or re-versioning one replays
  every contact once. A mapping declares the person statement groups it states
  (`aspects`), and on every pull-only record the base withdraws what the link
  states in a person group neither the ingest (`PartyManager.contact_aspects`)
  nor a registered mapping states, so a mapping no longer registered has its
  statements withdrawn on its replay. A `project` or `apply_record` that raises
  `ValueError` or `TypeError` for one record refuses that record alone
  (`mapping_refused`, its details naming the mapping, never the value), where it
  failed the whole page; it applies once its source changes. A backend's error
  payload passes its other keys to the refusal's details. The system check
  refuses a mapping of a backend that writes back (`parties.E002`), of no
  registered backend (`parties.E001`), or declaring no group or one the person
  has not (`parties.E003`). CardDAV and any backend without a mapping keep their
  payloads, comparison and mapping version.

- Sharing a directory shares its contacts. `parties/directory` has a stored,
  grantable `contact_reader` relation (shared through the record-access surface
  under the directory's `write`) and a `contact_read` permission: its grantees and
  the integration's owner read the people and organizations the directory's live
  links name, and the handles, addresses and employment edges those links
  currently assert, never another source's, never `read_private` or a `read__*`
  gate, and nothing of the directory, its integration, streams, links or
  credential. A tombstoned or unbound link, or a withdrawn or superseded claim,
  lends nothing. Schema revisions 21 to 24: a database synced at an intermediate
  revision, where the derived permissions were named `read_contacts` (and the
  party's `contact_reader` at 21), retires those names through provision's
  `--skip-checks sync --prune-stale --yes`.

- A statement member declared as a new foreign key keeps the `related_name` it
  declares (`StatementRecords.copy_fields`); one that declares none stays hidden
  as before. The parties handle member declares `%(class)s`, so `Handle` reaches
  its party statements as `partystatement`, the same spelling Django gives the
  statement subjects' reverses. A host generates one state-only `AlterField` on
  `partystatement.handle` with `makemigrations parties`; Django issues no SQL.

- The directory backend seam is neutral of CardDAV. `ParsedContact` loses `uid`,
  `etag`, `raw_vcard`, `href` and `department` (a backend keeping wire facts
  subclasses it, as CardDAV's `ParsedCard` does); `ParsedAddressbook.href` is
  `key`. A backend returns payloads keyed by external key (`observe`,
  `extract_contacts`, `read_contacts`, `contact_keys`, which seeks past `after`)
  and the base builds and applies the records (`record_changes`). Its
  `direction` is pull-only unless it declares otherwise; a pull-only stream
  compares what its source last applied, withdraws a confirmed-absent contact's
  statements and lets a payload name the keys a source merge replaced.
  `DirectoryBackend.probe()` is folded into `test_connection()`, which
  `Directory.test_connection` forwards to; the
  `remote_delete` stream option, validated and never read, is removed. CardDAV
  links need no migration and no re-download.

- A merge at the source keeps a pull-only directory's pages to one record per
  payload: `record_changes` no longer appends the replaced keys, which leave the
  source's key walk so the absence sweep tombstones their links, and an identity
  read of one answers it gone. The survivor lands on the first replaced record's
  person in the source's order (`observe` now keeps `replaces` in that order), and
  what only a replaced record stated on that person is withdrawn rather than moved
  to the survivor's link (`ingest_contact(..., replaces=...)`). One function
  decides a contact's target (`DirectoryBackend._contact_target`), once, under the
  contact locks; a trashed person is nobody's target
  (`PartyManager.contact_people`), so its record lands on a new person instead of
  quarantining. `ingest_contact` and `ingest_organization` decide nothing: they
  land on the target given or mint one, and refuse to move a link off a live
  party, `target=None` included. `DirectoryBackend._local_states` is public as
  `local_states`, the binding seam for a backend that builds its own records, and
  `lock_contact` takes several parties. CardDAV admits only Basic-auth
  credentials and names its server by host name and port in its connection
  message. Its configuration is a typed config model (`CardDavConfig`:
  `server_url`, and per address book a `ContactsPolicy` whose only key is
  `local_delete`), so every save of a directory validates the keys it knows and
  drops the others; a book's stream reads only what the policy knows, so a stored
  setting the write never validated
  no longer fails the sync (it did since the seam change). The directory's config
  form shows the server URL and the book policies through the JSON control.

- Connecting a directory is one verb, `Directory.objects.connect(owner, *,
  backend_class, display_name, credential_kind, material, config=None)`
  (`DirectoryManager`): the backend admits the unsaved credential and tests the
  connection outside any transaction, a refusal is a `ValidationError` with the
  backend's own operator message and nothing is saved, then the credential and
  the directory are created atomically, the address the account signs in as
  (`DirectoryBackend.account_address`, new, `""` by default) becomes the owner's
  own handle, and the first sync is requested. CardDAV's connect mutation keeps
  its GraphQL contract and calls it (its `connect.py` is gone), so connecting a
  CardDAV account now queues its first sync, which it did not before; a refused
  connection test now reaches the client as a `VALIDATION` error carrying the
  server's refusal instead of `INTERNAL`. The `carddav` vendor is a master
  resource adopted by slug: load resources before connecting the first one.

- A directory's credential rule is integrate's one admission rule:
  `DirectoryBackend.credential_blocker(credential)` returns a public-safe
  refusal or `None` (the base admits any credential), `Directory.credential_blocker`
  forwards to it, and `Integration.admit_credential` raises it on every
  attachment, so the connect picker lists a credential the backend refuses as
  refused instead of refusing it only once attached. `DirectoryBackend.admit_credential`
  and `Directory.admit_credential` are removed: a vendor backend implements
  `credential_blocker` (CardDAV's Basic-auth rule moved there), and an
  `admit_credential` override on a directory backend is no longer called.

- A pull-only directory adopts an existing person (rulings R2 and R9; an option,
  off by default, since the entry above): a contact
  with no person of its own or of a record it replaced lands on the first person
  its emails name (`people_for_emails`, preferred email first) whose adoption
  `PartyManager.adoption_blocker` admits; otherwise it mints its own. A two-way
  directory, CardDAV included, never adopts. The rule sits on the binding owner:
  `ingest_contact` refuses a person new to its link that no replaced link names
  unless the rule admits them, so no caller of the ingest verbs binds past it
  (integrate's own link writers do not ask it: a vendor overriding `apply_record`
  and returning a target is trusted code). It admits a person only when the stream is
  pull-only, the person is no account's own, the address that found them is no
  account's sign-in address, the directory's owner holds the new `adopt`
  permission on `parties/person` (owner or editor, no platform-admin arm: every
  directory's owner is a platform admin, so `write` admitted everyone), no other
  live link of the directory names them, and no live link of a stream that writes
  back names them. A pull-only contacts page locks every spelling of its contacts'
  addresses (by normalized value), the people they name and the parties its
  links name before any record decides (`DirectoryBackend.prepare_page`), and
  each record checks admission again under those locks. The handle that found the
  person is the evidence of the directory's identity statement
  (`ingest_contact(..., through=handle)`), restated on later records, and the
  contact's spelling of that address lands on that handle row. The link's
  `adopted_through` metadata and `angee.parties.backends.ADOPTED_THROUGH` are
  removed: the driver replaces link metadata wholesale. Schema revision 25:
  `rebac sync` after compose. Deferred, open with the architect: a source states
  whole aspects, and survivorship applies a winning empty value, so a field of an
  adopted person that no statement holds (written as system work, or stated by a
  lower-ranked source) is cleared by a contact that lacks it. Provenance has no
  option that keeps such a value for an adopted person without changing
  survivorship for every source; the expected behaviour is a strict expected
  failure in the pull-only directory tests.

- A pull-only contact never moves an address away from its holder. An email the
  lookup answers with a person other than the contact's own, who does not hold it
  too, is stated as a claim for review (confidence 0.4, below
  `Party.REVIEW_CONFIDENCE`), without touching the handle's display fields, and
  each such person is proposed as the contact's same person by
  `parties.directory_match`; one the contact claimed stays a claim while nobody
  holds it. Before, a refused contact's minted person stated the address in full
  as a directory source and resolution moved it, and what is attributed to it,
  off its holder; so did a second contact colliding with an adopted person. The
  ingest owner (`ingest_contact`) now makes these proposals, so the backend's
  decision proposes only the other people a source merge's replaced records
  name. A two-way directory (CardDAV) still states every address in full: its card
  projects only applied handle statements, so a claim would drop the address from
  the card, and what a two-way card publishes is open with the architect.

- `people_for_emails` reads and never writes: on the account basis it answers an
  account's existing person only. It used to create a person for every matched
  account that had none (`for_user`); a caller that needs the account's person
  asks `PartyManager.for_user` itself. `EmailHolder.person` and `.handle` are
  typed as `Person` and `Handle | None`. The connections import's fallback (the
  person its earlier run linked) passes over a trashed person, so such a
  re-import mints a new one.

- `Directory.objects.connect` checks that its owner may create a directory
  before anything else. Each connected directory gets a credential of its own,
  where a second connection of one display name used to update the first one's
  credential. A backend whose vendor row is
  not loaded is a `ValidationError` that says to run `manage.py resources load`
  (it was an `ImproperlyConfigured`). The first sync is requested after the
  transaction commits, and a failure to queue it is logged instead of raised.

- A contact whose person was trashed lands on a new person, and its link now
  withdraws what it stated on the trashed person before it binds the new one, with
  the `directory_match` proposals pairing
  the trashed person with the new person or with whom the contact's addresses
  name. Before, those statements stayed asserted on the trashed person, and so
  did the pair a refused adoption had proposed for it. The binding owner
  (`ingest_contact`) does this on every rebinding, so the backend no longer
  withdraws a refused adoption's pair itself.

- A directory proposes for the same-as review each person its decision of a
  contact's target passed over: a candidate an adoption limit refused, and the
  other people the records a source merge replaced were linked to. The new
  install rule `parties.directory_match` (a `parties_statement_source`) states
  the pair (`PartyManager.propose_directory_match`), citing the contact's link
  and the handle or the replaced link; the Review page lists it and its merge
  and keep-separate verbs act on it. A pair kept separate is not proposed again,
  an unchanged one is not restated, and merging the two (`Party.merge_fields`)
  or the contact later adopting the refused person withdraws it
  (`withdraw_directory_match`). `reconcile_same_as` withdraws only its own rule's
  pairs, so it leaves these standing; that it never proposes two people who
  claim one handle row by itself stays open. Load the install tier before a
  directory's next sync, since the rule is looked up by key.

- A person's merge never sets two live contacts of one pull-only directory on
  one person (default pending ruling O16). A survivor takes every link of the
  records merged into it, and two bound, live links of one pull-only directory
  from different records would put two records the source keeps separate on one
  person, whose statements overwrite each other on every sync:
  `Party.validate_merge` refuses it (`PartyManager.directory_merge_blocker`) with
  a message that says to merge or remove one of them at the source. A tombstoned
  or withdrawn link does not count. Any other merge of people the merging person
  writes goes through, an account's own person and a person a two-way card names
  included.

- `PartyManager.people_for_emails(emails)` answers who email addresses name: per
  address, in the caller's preference order, an `EmailHolder(email, person,
  basis, handle)` for each basis that names someone (`EmailBasis.ACCOUNT`, the
  account's own person; `HELD`, the live person holding a handle of the address
  through merge survivors; `CLAIM`, whenever no live person holds it, the one
  person a current asserted contact claim proposes it for, so an address an
  organization or a trashed person holds still finds the person claimed for it).
  Handles compare by `normalized_value`; a trashed
  person, an organization and a merged-away row are nobody's answer; any batch
  reads in a bounded number of queries. The connections import finds a handle's
  holder by the same rule, so a handle an organization or a trashed person holds
  now falls back to the person the import's earlier run linked instead of minting
  (organization) or landing on the trashed row unless the import linked it.

- Parties land organizations and employment from a source.
  `PartyManager.ingest_organization(parsed, *, stream, link, created_by_id,
  target=None)` lands a `ParsedOrganization` (comparison shape:
  `organization_projection`), states its legal identity whole or withdraws it
  when the source carries neither field, and like `ingest_contact` refuses a
  target of another kind or one that would rebind a bound link.
  `RelationshipManager.land_employment(person, employers, *, created_by_id,
  asserted_by, link, evidence=(), kinds=None)` replaces the free-text-only
  `land_employment(person, *, organization, title, ...)`: an `Employment` names a
  tracked Organization or a free-text name, a link may name several, an employer
  named twice lands once, and the link's edges it no longer names lose its
  statement. A connections import that renames or drops an employer now renames
  or withdraws the link's own edge instead of keeping the old one asserted.

- Creating a `parties.Address` or `parties.Relationship` takes write on its party
  (`create = party->write + admin->member`, evaluated on the unsaved row; a
  relationship's party is its anchor). It was `authenticated`, so anyone who could
  read a party (an identity reader, a per-row reader, a directory's contact reader)
  could attach a row its owner does not read, and a two-way directory wrote it to
  the remote card. Sync and imports run under system context and are unaffected.
  A caller that attached rows to parties it only reads is now refused: the
  console's Relationships page and the `create_relationship` MCP tool offer any
  readable party, and so does a consumer page with a party picker over
  `parties.Address`. The permission is schema revision 23: `rebac sync` after
  compose.

- Parties: a person merges duplicates in a batch,
  `Party.objects.merge_duplicates(actor=, sets=, certain=False)`, the
  `merge_duplicates(sets, certain)` mutation: confirmed sets of two to ten
  records (at most 50 sets), from the certain pairs the Review page lists
  (`certain_same_as_pairs`, `certain=True` re-checks each) or a selection from a
  list. `duplicate_set_previews(sets, certain)` reads what the dialog shows
  (members oldest first with their statements and handles, the survivor, a
  refusal, and, for a set that merges, the fields the records disagree on with
  the value that will show, each field behind its own `read__<field>` gate per
  record) in a fixed number of queries. Only an active person account writing
  every record may (the party's new `batch_merge` permission,
  `write & person->active_member`, parties schema revision 30), read on the
  party whatever manager is asked; no agent tool offers it. A set holding an
  account's own person, two kinds, a trashed or merged record or two records a
  person kept apart refuses with its reason; a set of more than ten records, one
  holding a record the person may not read, or one the person may not merge in a
  batch is refused before anything is read further or locked; a set merges exactly as confirmed, judged again under
  one lock pass of its members, their same-person partners and their handles; the oldest record survives and the others fold into it in
  key order; each set runs in its own transaction: a lock conflict (deadlock,
  lock not granted) is that set's `stopped`, and any other database failure (a
  statement timeout, a lost connection) its `failed`, with a reason for the
  person and the error in the log, and the next set still runs. A batch records no value
  as the person's choice (`PartyManager.BATCH_CONFIRMED_FIELDS`): statements move
  as their sources' own and an empty survivor field takes a merged record's
  value as a plain value (`Party.merge_values`). A pair is certain when both are
  people, neither is an account's own person, exactly the two are linked to an
  address it cites, and their names are equal but for case, accents and hyphens
  (`Party.comparable_name`); a name that is one of the pair's cited addresses
  (`Party.names_an_address`) is no name. Every party merge now refuses a person
  with an organization (`Party.validate_merge`). A person's `anniversary` is a
  merge choice (the Merge page shows it). The web fragment's `DuplicatesDialog`
  is the one batch surface: the Review page's "Merge certain pairs" action, and
  the People and Organizations lists' Merge…, where one selected record opens
  the person form's "Merge into…" (`useMergeIntoAction`, shared by both), two
  open their compare page, and three or more open the dialog as one explicit
  set, the whole selection.

- Parties: `Party.objects.propose_same_person_as_system(left, right, *, evidence,
  reason)` lets an importer propose that two parties are one person, citing the
  handle rows that link them. It elevates, so it takes a named reason. It issues no
  query and takes no lock in the caller's transaction: it registers the proposal
  with `transaction.on_commit`, so a rolled-back caller proposes nothing, and runs
  it after the commit in its own short transaction and changeset, returning
  nothing. There it reads each cited handle as its merge survivor, requires it to
  link one of the two and the two to be a same-person pair at a cited address, and
  states the `parties.same_as` rule's normal pair statement, the one
  `reconcile_same_as` states, keeps and withdraws. It writes nothing when the two
  are one party, either is trashed or merged away, the pair already holds a
  current assertion or a keep-separate decision, or the evidence does not hold; it
  never merges, and a failure after the commit is logged for the job to heal. A
  pair now locks both parties in one identity pass, Handles before Parties
  (`StatementsMixin.lock_subjects`, which `Party` implements and its merges use);
  locking one party after the other ran against the Handle, Party order. A party
  merge also takes its same-person partners and every Handle its parties hold in
  that pass (`Party.lock_merge_records`): the fold re-keys the merged party's
  other pairs onto the survivor, which locked each partner's Handles after party
  rows and could deadlock with a resolution of the partner's address (a defect
  already on `main`); the pass reads the partners again under its lock and tops
  the set up when a pair committed while it waited. The job
  no longer withdraws a proposal stated after its run began, and restates a
  standing proposal whose cited handle was deleted or merged away with the handles
  that still link the two, so it never hides from the review list. Only the
  rule's own undecided proposal is restated: a pair a person kept apart or
  confirmed spends none of the run's budget, and a decided pair is read before
  it is locked, so it takes no lock and makes no pair row.

- Parties proposes a same-person pair for every two live parties of one kind
  linked to one address (`PartyManager.reconcile_same_as`, over
  `same_person_links`). A party is linked when it holds a handle of that
  normalized value or when a source that states its own record names one for it
  in a standing statement, a claim below review confidence included; before,
  only two holders of two spellings were proposed, so one address claimed by a
  second source's party was never proposed. The linking sources are an
  allowlist: `PartyManager.LINKING_SOURCE_KINDS` (a person, a system of record,
  a directory, an import) and `PartyManager.LINKING_RULES` (a source's claim, a
  holder found on its row); any other rule or extraction, parties' suggesters
  included, links nobody until it is named. Only a handle that names one account
  links (`Handle.identifies_account`: an email address, a phone number, or a
  platform id), so two friends an export records by one name are never one
  person. A person is never paired with an organization, nor two accounts' own
  persons with each other, and a contested control claim links nothing. An
  address more than `PartyManager.SHARED_ADDRESS_LIMIT` (four) parties are linked
  to, or one the shared-sender providers name, is shared and pairs nobody, and a
  pair needs two sources between its parties, so one source's two records of one
  address are not proposed. The job finds candidate addresses in SQL, reads only
  their links, looks the rule up once, and withdraws exactly the rule's pairs no
  link supports; the hourly task reads the shared-sender providers once for both
  passes (`PartyManager.shared_sender_ids`, the new `shared=` argument of
  `reconcile_suggestions` and `reconcile_same_as`). The review list
  (`same_as_pairs`) pages by cursor: `same_as_pairs(limit, after)` returns the
  pairs after the pair `after` names, in the pair rows' key order, each with
  its `id`, so a page continues after the last pair seen and a pair merged or
  kept apart meanwhile moves no other one. Which pairs a reader may review
  (both endpoints and every cited handle readable) is decided for every pair at
  once in a fixed number of queries, and only the page is read in full; a pair
  the reader cannot read never ends a page early. `certain_same_as_pairs(limit,
  after)` gives the certain pairs a batch at a time the same way, and
  `same_as_pair_counts { pairs certain }` (`PartyManager.same_as_review_counts`)
  says how many of each the reader may review. The Review page shows both totals
  and gives each of its lists its own pager (the claims list embedded in its
  section, the pairs under their cards); "Merge certain pairs (50 of 180)" names
  the batch and the dialog says the next batch follows; the overview counts every
  pair instead of a capped read. In the dialog each set that may merge has its
  own Include toggle, off by default when the set carries a warning (a
  disagreement, a member that no longer matches its group) and off again when
  the set changes from the state the person saw or ticked it in, and the
  mutation sends only the included sets. A disagreement is a field the records
  state at least two different values for; a field one value is stated for,
  the rest empty, is a fill (`DuplicateSetPreview.fills`, the same
  `MergeFieldValues` rows), which the merge gives the survivor and which warns
  nobody. The group-match mark reads either list, so a member whose grouping
  value was cleared since the selection still no longer matches its group; when
  every member moved to the same new value nothing differs and nothing is
  marked. Each set that may merge carries a `digest`
  (`DuplicateSetPreview.digest`, `keyed_facts_digest`, the keyed hashing
  `IdentityBasis.facts_hash` now shares) of what the dialog shows of it: each
  member's id, name, creation time and first-met note as the reader's gates
  leave them, the readable handle statements with their source and handle, the
  survivor and the disagreements with the values that will show. The mutation
  requires the digests (`merge_duplicates(sets, digests, certain)`), and a set
  whose digest no longer matches under the lock is refused with a reason that
  asks the person to look again.

- Parties: a handle's holder is always the projection of a statement. A holder
  written onto the row with no statement behind it lost its address the moment
  anything resolved the handle, a claim below review confidence included.
  `Handle.objects.upsert` now refuses `party`, `party_id` and
  `party_link_confirmed`; a seeded handle row naming its holder is stated by the
  new `parties.stored_holder` rule after its load
  (`HandleManager.state_stored_holders`), and a seed that moves or clears a
  holder withdraws what its earlier load stated for the old one; and the
  `resolve_stale_owners` repair states every such holder before it re-resolves
  stale ones, where it used to clear them. The rule is the last kind the handle
  aspect ranks (below a person, a system of record, a directory and an import),
  so the repair leaves a confirmed holder and one another party's applied
  statement contests as they are, for a person, rather than clear the
  confirmation or move the address; it leaves a trashed holder's address alone,
  neither stating nor re-resolving it (resolution ignores a trashed party, so
  either would clear the row), states it after a restore, and states a
  merged-away holder for its survivor. It returns a `HolderRepair`, and the
  command reports how many it stated, left confirmed, left contested and
  re-resolved (`-v 2` lists the handles left). Every provision runs it
  (`PartiesConfig.compose_provision`), after the install resources load the
  rule; it is idempotent. One run writes at most `HandleManager.REPAIR_LIMIT`
  (500) holders (`resolve_stale_owners(limit=)`, the command's `--limit`); past
  the cap the command's last line, in the provision log too, says how many
  remain (`HolderRepair.remaining`) and the next run continues. Test fixtures
  that create a handle with `party=` still write a bare holder.

- `angee.integrate`: a stream's mapping can be composed from several declarers.
  `StreamDefinition.mapping_contributions` declares one
  `MappingContribution(key, version, sequence=...)` per part; integrate orders
  the parts by sequence, then key, and `SyncStream.mapping_contributions` retains
  the ordered `[key, version]` list a generation ran with the version it was
  assigned, `{"version": V, "parts": [...]}` (read through
  `SyncStream.retained_contributions`). Any change of that list (a part added,
  removed, exchanged, reordered or re-versioned), or a list that no longer names
  the stream's version, opens one baseline under a version above the retained
  one, above every version the stream's records were applied with and at least
  the parts' sum, so each record replays once and a composed version never
  lowers. A renumbering that keeps the order changes nothing. A plain
  `mapping_version` keeps its rule, rollback included; declared on a stream that
  composed, it reads as one part under the empty key. A declaration with
  contributions leaves `mapping_version` at its default, and a malformed
  declaration is refused with `ValidationError`.
  Upgrade: a host runs `makemigrations integrate` for the `mapping_contributions`
  column (one `AddField`, `JSONField(blank=True, db_default={}, default=dict)`),
  then `migrate`. Existing streams hold the empty mapping and keep their
  generation, cursor and version; nothing is read again until a stream first
  declares contributions, which replays its records once. The database default
  lets a release before the column, rolled back onto the migrated database,
  still create stream generations.
  Rollback: the stored `{"version", "parts"}` format is forward-only. Code from
  this column's first form, which stored a bare `[[key, version], ...]` list,
  reads `{}`, `[]` and bare lists, but fails on every composed stream this
  release wrote: that stream's partition errors on each sync and writes nothing.
  Before or after rolling back to it, run once on the host's PostgreSQL
  database. The statement is idempotent and safe while the stack runs:

  ```sql
  UPDATE integrate_syncstream
  SET mapping_contributions = CASE
      WHEN (mapping_contributions ->> 'version')::bigint = mapping_version
      THEN mapping_contributions -> 'parts' ELSE '[]'::jsonb END
  WHERE jsonb_typeof(mapping_contributions) = 'object' AND mapping_contributions ? 'parts';
  ```

  A stream whose stored version is its own gets its bare list back, so it
  replays nothing under that code. Any other stream gets `[]`, which that code
  reads as never composed, so it replays once. Returning to this release then
  replays each composed stream once, because a bare list carries no version.
  Clearing every composed row to `[]` also recovers, but replays all of them.
  It also leaves any stream the older code declares plain on the plain rule,
  which can lower that stream's version onto a number its composed records
  already carry, so those records are never replayed.

- `angee.integrate.http` owns a vendor's typed JSON request
  (`HttpClient.request_model`) and one rule for a refused answer
  (`HttpRefusal`): a transport failure, a 408, a 429 or a 5xx other than 501
  and 505 is transient and waits for `Retry-After` (`is_transient_status`: a
  408 may be repeated, a 501 or 505 refuses the request itself); a transport
  failure after sending is worded "<service> did not answer.", a connect
  failure "<service> could not be reached."; a success that is oversized or unreadable, a body httpx2
  cannot decode included, is permanent; an adapter that reads one record per
  request makes that record's oversized or unreadable answer the record's
  refusal (Notion does). The status decides before the
  body: `HttpClient.request` keeps a refused answer's status, with no body,
  when its body is over the cap or httpx2 cannot decode it, never reading past
  the cap, and still follows a redirect hop whose body is over the cap.
  `HttpRefusal.as_error` builds the
  vendor's `IntegrationError`, with the vendor's own words, transience and wait
  when it states them (a wait implies transience); a refusal keeps the answer's
  headers, without credential or cookie headers and out of its `repr`, for a
  vendor's own signal, and its `cause` tells a
  request not sent from one whose outcome is uncertain. A request the HTTP
  library will not build, a header value it cannot encode or will not send, is
  `RequestNotBuiltError`: not sent, permanent, worded as a request that could
  not be sent as configured, and chained to none of the library's text, which
  quotes the value; callers read a not-sent failure's `transient`, so OAuth
  answers such a token request with a permanent 400 and leaves such a
  revocation best-effort. Notion, the Google
  client (Drive and Calendar), ICS, GitHub and OAuth's JSON reads (discovery,
  userinfo, JWKS) moved onto it, and `HttpClient.get_json` is removed. GitHub's
  refusals are now `IntegrationError`s, a 429 or 5xx transient, and its rate
  limit (a 403 or 429 with no requests remaining, or with a `Retry-After`)
  transient until the window resets; OIDC discovery
  keeps a provider's 5xx or 429 transient instead of a permanent configuration
  error; Google's oversized and unreadable answers are no longer retried; a
  Notion page over the cap or unreadable is quarantined instead of stalling its
  stream. The posts feed's `request_json` sends through the refusal hook and
  leaves integrate the status rule and the wait: a refused answer its adapter
  does not name is integrate's refusal, so an unnamed 429 is transient and
  waits (it was a permanent "refused" that ignored `Retry-After`), and a 3xx a
  read did not follow is refused instead of returned as data; a GET's oversized
  answer, and a GET success that is not a JSON object or that httpx2 cannot
  decode, are permanent; a POST's 3xx is uncertain. A refusal factory that
  returns the failure it was handed has it raised unchanged. Remaining:
  `download_bounded` (with `download_capped`) and `stream` still signal a
  refusal with `None` or a raw transport error, so calendar feeds, media and
  attachment downloads and page analysis classify a failed download
  themselves.
  Google Drive and Google Calendar word a file's or an event's refusal in their
  own phrases, never Google's reason. Capped JSON requests, GitHub's and
  OAuth's included, ask for an unencoded answer, `is_transient_status`
  reads only 500–599 as server errors, and a typed request answered with no
  JSON body (a 204) is unreadable.

- The CardDAV directory's requests compose that rule too
  (`HttpClient.request(error=)` and `HttpRefusal.as_error`). A transport
  failure, a 429 or a 5xx is now transient and waits for `Retry-After`; before,
  a refused answer reached the operator as a permanent "CardDAV <method>
  returned HTTP n." and a transport failure as a raw error. Integrate words every
  other refusal, and a multistatus httpx2 cannot decode is a permanent unreadable
  answer. CardDAV keeps only its own answers: a 412, or a 403 to a write, sets
  that card aside; a cursor's 404, or a 403 naming `DAV:valid-sync-token`,
  restarts the stream; a success over its byte cap splits a multiget, and a
  listing over it says to use smaller address books. A tokenless book's listing
  refused only for now stays transient instead of reading as a book with no
  complete ETag listing, and a vCard photo's refusal keeps its words and takes
  integrate's transience.

- `angee.integrate`, `angee.workflows_integrate`: static-token and workflow loose
  ends. Testing a connection whose static token is not a header (an ICS feed
  address with a space or a non-ASCII character) failed with "Credential is not
  usable", because the default probe built a header; the static-token probe now
  checks the token is active and readable without one. An inactive static token
  raised a plain `ValueError` on the header path, which a sync reported as an
  unexpected failure; it raises a permanent `IntegrationError` asking for the
  token again. A workflow step waiting for a discovery whose retry time had
  passed woke every second and noted the same message each time; it backs off,
  doubling from one second, and notes once per discovery attempt.

- `angee.integrate`: the lock order is completed. Deleting an integration locked
  the parent row with its child in one statement, the reverse of
  `Integration.lock_capability`; `Integration.lock_for_delete` now takes the
  capability lock first, on every delete path. An account's disconnect took the
  integrations, the credential and the user before the account an OAuth connect
  locks first; `CredentialManager.disconnect_account` now locks the account row
  first. The README's lock order names the external account (first) and the
  credential's user (after the credential). The savepoint retry in
  `CredentialManager.lock_with_served_integrations` treats an integration deleted
  between its read and its lock as a changed set, and gives up after three changed
  sets with a transient `IntegrationError`. An unchanged re-entry of a
  `jwt_bearer` key compares what was entered, not the cached scoped tokens the
  re-entry drops, so it no longer sends the integrations it serves to discover
  again. The PostgreSQL tests of the two inversions run only on PostgreSQL.

- `angee.integrate`: derived credentials. `CredentialManager.store_derived` now
  overwrites a credential of the derived name that nothing holds, so a row left
  from an integration deleted before derived credentials left with it no longer
  fails every later discovery of that name; it still refuses one something else
  holds. `CredentialManager.unattached` derives what holds a credential from the
  model's own reverse relations (each foreign key under its live policy), so an
  agent's inference credential or an MCP server's is no longer deleted, and
  silently nulled, when the integration that derived it leaves. The delete
  cleanup reads the integration's links from its locked row (a `pre_delete`
  receiver takes the delete lock, as every delete path then does), covers an
  integration whose source grant was lost (`Integration.derived_credential_id`),
  and locks the credential before deciding (`delete_unattached_derived`).

- `angee.messaging`: outbound delivery waits for connection discovery.
  `run_message_delivery` published through a channel whose integration was
  discovering again, so a queued reply on a feed that requires discovery went out
  with a grant discovery had not confirmed. The delivery now asks the transport
  channel's integration (`Integration.require_discovery_ready`) before it sends:
  a pending discovery defers it as lock contention does, spending no attempt, and
  a failed discovery fails it with the discovery's own message.

- `angee.integrate`: the discovery check on stream commits covers what rests on
  remote facts, and says why it refuses. Pinning, unpinning, dismissing a failure
  and reopening a conflict whose follow-through was refused change local state
  only; they were refused until discovery succeeded, and the reopen left the
  conflict closed with neither side applied. They now commit through
  `angee.integrate.states.exempt_from_discovery()`, the one named way past
  `lock_current`'s check. So does the reflection of a write already sent (and a
  remote's refusal of it): a write that landed while a secret was re-entered is
  recorded, so the next run neither repeats it nor conflicts with it. A two-way
  page now asks before each write-back, not once. A discovery that failed
  refuses permanently with its own operator message instead of a transient "must
  finish", so a workflow sync step fails with it. The check before a remote write
  is one read. Retrying a failure still waits for discovery. Known limit: an
  adapter that reads its credential again between the check and the send can send
  that one write with the new secret.

- `angee.integrate`: a re-entered secret is discovered again.
  `Credential.replace_material` kept every attached integration running on the
  facts its connection discovery found with the old secret, so a key of another
  remote account was read with the old account's cursor, links and identifiers.
  Now each integration the credential serves (attached, or derived from it) whose
  capability requires connection discovery awaits it again in the same
  transaction, locked before the credential as an attachment is
  (`CredentialManager.lock_with_served_integrations`), and sync waits for it. An
  `apply_discovery` that refuses another account lands as failed discovery with
  its operator message. Refreshed and minted tokens and transient material
  (`Credential.update_material`) keep discovery. `Bridge.subscription_state` is
  documented as holding the connection facts discovery learned, beside
  live-session state.

- `angee.integrate`: a secret re-entered in place or by name, or an account-less
  credential adopted by its first account, is discovered again.
  `CredentialManager.create_local_credential` with a name the user's credential
  already has (the console's create, connect flows that key their credential by
  name) replaced its material, and even its kind, without the integrations using
  it discovering again. That update and `Credential.replace_material` now share
  one owner, `CredentialManager.reenter_secret`. It refuses a credential an
  integration attaches as derived material; when the kind or material changed,
  every integration the credential serves admits it again
  (`Integration.admit_credential`; a refusal rolls the re-entry back) and those
  whose capability requires discovery await it again; an unchanged re-entry
  changes nothing. Adopting an account-less OAuth credential sends the
  integrations attaching it to discover again. Discovery stores the material it
  derived through `CredentialManager.store_derived` instead, which sends no
  integration to discover again, so two that derive a credential of one name do
  not loop, and refuses to overwrite a hand-made credential of that name.
  `Credential.update_material` refuses the fields the kind stores as its own
  secret (`CredentialKindHandler.secret_material_fields`: `api_key`, `password`,
  …, every token an OAuth grant stores, and for `jwt_bearer` the signing fields
  its key file becomes) and points to `replace_material`; both judge by the kind
  of the row as locked, so a concurrent kind change cannot let a stale instance
  clear the real secret. A token refresh, a minted scoped token, transient material
  and the reconnect of an account already bound keep discovery. The integrate
  README states the lock order once: `CredentialManager.lock_with_served_integrations`
  takes the integrations a credential serves before it on every path (re-entry,
  reconnect, adoption, `Credential.lock_for_delete` for its deletion and
  disconnect), and takes every lock again when what the credential serves
  changed between its two reads.

- `angee.integrate`: an owner can transfer their own integration.
  `Integration.transfer_ownership` released the previous owner's connection on
  an instance still pinned to that owner, who lost write with the row, and a
  pinned actor outranks the system context, so an owner's transfer of an
  integration running on their own credential was refused. The release now runs
  on the locked capability row (`Integration.lock_capability`). The transfer
  also locked the parent row before the concrete one, the reverse of bridge
  work; `Integration.lock_for_record_access` now takes the capability lock
  first, for a transfer and a record grant alike.

- `angee.integrate`: a static token is clean, and dead when revoked. A token was
  stored exactly as typed, so a pasted trailing space or an invisible character
  made a header value no request could carry, failing every request. The
  `static_token` handler now strips surrounding whitespace on creation and
  re-entry and otherwise stores the value as entered, since some static tokens
  are never a header (a calendar feed subscription keeps its feed address as
  one). Where it builds the bearer header, a token with a space, a control
  character or a character outside printable ASCII is refused with a permanent
  `IntegrationError` saying the stored token cannot be sent and must be
  re-entered, without quoting it. A revoked or expired static-token credential no
  longer produces authorization headers: it fails closed through
  `Credential.require_active`, as the scoped-token path does.

- `angee.integrate`: an integration's refusal reaches the operator as its
  message on integrate's own console actions. Retrying a sync failure, choosing a
  side of a conflict, retrying discovery or resuming an integration let an
  `IntegrationError` propagate, so the console showed a raw "An unexpected error
  occurred." Those actions are now guarded by
  `angee.integrate.schema.integration_action_guard`, `action_guard` with the
  integration error expected: it answers in band with the error's public message,
  keeps an `OAuthFlowError`'s code, and logs a permanent refusal at warning
  level. The shared guard, `ActionResult.from_error` and `angee.base.exceptions`
  are unchanged, so no other addon's guarded action changes behaviour.

- `angee.integrate`: a derived credential leaves with its integration. Deleting
  an integration left the credential its discovery derived, which
  `CredentialManager.store_derived` then refused to overwrite, so the next
  integration deriving the same name failed discovery until an operator removed
  it. A `post_delete` receiver (bound through `angee.base.signals.connect_for_models`
  from `IntegrateConfig.ready`) now removes it in the delete's transaction, after
  the integration row, unless another integration still uses it. The refusal
  stays for a hand-made row or one left from before, and names the credential
  to remove or rename.

- `angee.workflows_integrate`: a sync step waits for a pending connection
  discovery instead of failing. The discovery refusal reached `bridge_io`'s
  catch-all as a retryable "Stream transport failed.", so a step was failed
  within seconds of a re-entered secret, with the wrong message, before
  discovery could finish; a single-sync bridge's attempts also held the lock
  discovery needs. `bridge_io` now asks the bridge before any IO or lock: a
  pending discovery parks the step in a time wait until discovery's next attempt
  is due, without spending an attempt or beginning an effect, and notes the
  discovery message; a failed discovery fails the step with that message. Known
  limit: a step already in its IO when discovery turns pending spends that one
  attempt before the next one waits.

- `angee.integrate`: a sync run already in flight sends and commits nothing once
  a secret is re-entered or a credential attached. Admission was fenced, but a
  run admitted before the change read the fresh credential and went on with the
  facts discovered from the old one. Every commit to a stream or its links passes
  `SyncStreamManager.lock_current` (a page, a push's reflection, a sweep, an
  absence, a retained failure, a conflict decision), which now asks the stream's
  integration `Integration.require_discovery_ready()`; the driver asks again on
  the stored row before each remote write (a two-way page's write-back, each push
  candidate, `RecordLink.resolve_conflict`) and each reset (`reset_stream`, which
  `begin_stream_cycle` now resets through). A pending discovery refuses with a
  transient `IntegrationError`; earlier pages and their cursor stay, and the next
  run waits for discovery. An operator's resolve or retry of a retained failure
  and a calendar publish wait for discovery too. Known limits: a multi-partition
  workflow step runs without the sync lock, so a discovery that succeeds between a
  page's extraction and its commit lets that page commit; an attach on a
  capability that requires no discovery does not stop a run.

- The UX guard accepts manifest `vocabulary` field tones by structure while rejecting local tone maps and switches.

- Integrate compares synchronized JSON as JSON values (`json_equal` in
  `angee.base.serialization`), never with Python's `==`. A move from `[False]`
  to `[0]` was classified unchanged, because `False == 0` in Python and
  `DeepDiff` matched array items by that equality, so the remote's change never
  applied; a bool and a number now always differ. A number is the value its
  spelling denotes: `1` and `1.0` are one number at any depth (`DeepDiff` called
  `{"a": 1}` to `{"a": 1.0}` a change but `[1]` to `[1.0]` none), and a base
  whose numbers PostgreSQL's `jsonb` respells (`1e+16` reads back as
  `10000000000000000`) still equals the projection that wrote it. The stream
  fence and the sweep's cursor-progress checks compare cursors the same way.
  `angee-integrate` no longer requires `deepdiff`.

- Authored row actions declare `toast.successTitle` and `toast.failureTitle`
  separately; `toast.description` remains the fallback failure reason. Callers
  migrate from the former shared `toast.title` so successful and refused verbs
  report the right outcome.
- Message delivery recovery controls consume `delivery_actions`, projected from
  the same retained-state admission check as Retry and Mark as sent. Task Wake
  joins `task_actions`. Project permissions expose `transfer`; files expose the
  sealed `inherit_visibility = drive->share` for the existing widening rule.
- Record verbs, Delete, Archive and Trash are supplied by the shared views
  instead of each page.
  - **Passwords and credentials.** Change password uses the login throttle and
    retains the requesting session. Administrators Set password through a
    revision-checked account verb sealed to platform-admin membership and both write gates:
    another person's protected account (including staff and superusers) is admitted;
    self and service accounts remain refused, as do protected targets of Issue
    password and Reset password. Non-user subjects are refused; successful sets log
    only the verb and actor/target public ids after commit. **Contract change:**
    raw `users` updates no longer accept `password`; use `set_user_password`.
    Creation keeps its password input. Typed creation, set and change share
    Django validation. Non-OAuth credentials expose name editing and an
    administrator-only Replace secret verb that requires nonempty material,
    removes stale keys and preserves attachments; expiry stays read-only.
    Shared forms preserve multiline keys, declare autocomplete and use transient
    mutation handling. The user menu preserves dialog lifetime and focus and
    hides Change password when the session has no usable password or is in view-as;
    preview identity reads return null for password usability.
  - **Toolbar.** Explicit placement wins; `primary` implies the toolbar. On a
    saved record, the form's own band then fills to two buttons with its visible,
    unplaced, non-danger verbs in declaration order, counting verbs already on
    that toolbar. Filled buttons stay secondary; the rest go to `⋮`, after the
    verb buttons. `RecordActionBar` replaces `menuPlacement` with
    `menuTrigger="icon" | "label"` (default `"label"`); both triggers follow the
    buttons. An overflow menu is the last control of its group and never sits
    between two buttons, in any toolbar: `ActionMenu` owns the rule, and a menu
    behind the `more-vertical` or `more-horizontal` glyph orders its own trigger
    after every sibling of its row, whatever contributed them; menus behind
    other glyphs are unaffected. Contributed band buttons also render before
    it (`RecordActionBar` `contributedVerbs`), and the message reader writes its
    record chrome before its menu.
    A verb contributed through `#sections` stays in the menu unless it declares
    a placement. Create forms show Save / Create without record verbs.
  - **Delete.** `hasura_model_resource(delete=True)` generates
    `<res>_delete(id, confirm): DeletePreview`, served by the write backend's
    `delete_preview`, which `delete` composes; a `delete=True` resource whose
    backend lacks it fails at composition. Delete is gated by the resource's
    delete-preview root, including preview-only resources, without requiring a
    projected row permission. **Upgrade:** authored previews are removed and
    their roots renamed (`delete_calendar` → `calendars_delete`,
    `delete_calendar_event` → `calendar_events_delete`, `delete_agent` →
    `agents_delete`, `delete_user` → `users_delete`, `delete_vault` →
    `vaults_delete`, `delete_page` → `pages_delete`, `delete_folder` →
    `folders_delete`, `delete_channel` → `channels_delete`, `delete_note` →
    `notes_delete`, `delete_external_account` → `external_accounts_delete`,
    `delete_credential` → `credentials_delete`); a consumer's authored preview on
    a `delete=True` resource collides with the generated one and is deleted; a
    custom delete moves into its write backend's `delete_preview`. Preview and
    confirmation report live record links holding the target or a cascaded row
    through the same edge-owned rule as deletion; confirmation deletes nothing
    while a blocking link remains. Hold checks batch cascaded rows per edge and
    target type; a deletion-time refusal with no retaining rows still reports a
    readable blocker after rollback.
  - **Not delete surfaces.** The `integrations` inventory and the storage
    `files` resource are `delete=False`: deleting a channel's inventory row
    skipped its purge, and a file's delete was already the move to trash.
    `delete_integrations_by_pk`, `delete_files_by_pk` and `delete_file` are gone;
    a file is removed through `trash_record`.
  - **Archive.** A resource whose update root accepts its `archivable` flag gets
    Archive / Unarchive on the record and the selection bar, and an Archived
    header chip. Archived rows leave default lists, dashboards and relation
    pickers unless a filter names the flag; every such list offers an
    "Archived" filter. Pages drop their `is_archived` checkbox, grouping and
    picker filter.
  - **Flags and Trash.** Every resource whose model composes `TrashMixin` or
    `ArchiveMixin` exposes its flag, including multi-table children. Trash verbs
    act on the model storing the flag. A trashable resource gets Move to trash / Restore,
    and its Delete is offered only for a row already in the trash.
    Record Trash / Restore retain `allowsRecordPermission` gating.
    `useTrashActions` is no longer exported: the form supplies it.
  - **Bulk.** A page's `bulkActions` are added to the generic actions and no
    longer replace Delete. Every bulk action is gated by the resource's roots
    and flag contract, without projected row permissions; the server authorizes
    each write. Bulk is a client fan-out over the single verb, bounded at 25 rows
    a click (`BULK_ACTION_FANOUT_LIMIT`, distinct from the server selection limit).
    Outside embedded presentation, selection defaults on only for lifecycle
    actions, page `bulkActions` or a grantable model. A `source` list counts only
    page `bulkActions`; explicit `selectable` wins.
  - **Shared owners and API changes.** Metadata exports `resourceLifecycle` and
    `canDeleteRecord` beside `archiveFlagField` and `unarchivedFilter` in
    `packages/metadata/src/fields.ts`. Archive scope and the Archived filter
    (stable id `archived`) share filterability independently of update support;
    collection reads use one internal `useArchiveScopeFilter` hook.
    `allowsRecordPermission(record, permission, metadata)` derives the permission
    contract from resource or model metadata. Authored form Delete actions keep
    their own admission while sharing the generic trash-state rule. `RemovedList` refuses its default Restore
    without a resource; a domain may supply `canRestore`.
    The relation picker option `catalogue` becomes `includeArchived`.
    `useBulkAction` and `BulkActionRunner` are internal, and the unused
    `useTrashRowActions` is removed. `RecordActionBar` adds `fillBand` and
    `genericActions`; `useBulkDelete` takes the shared runner and optional row
    snapshots, and `useTrashPrompt` accepts a count.
  - Deferred: permanent delete in `RemovedRecords`, a transactional bulk verb,
    `permissions` on master-data nodes, and a delete of their own for
    `parties.Directory`, `posts.Feed` and `calendar.CalendarSource`, which were
    deletable only through the inventory root.

- Addons require django-compose 0.1.0.dev13, whose snapshot digest covers each
  addon's resolved path. A host that changes its sync from `--no-editable` to
  editable, or moves a source checkout, now recomposes on `compose --if-stale`;
  before, the receipt stayed current while the web manifest's roots named the old
  location, and the frontend build failed on a missing addon entry.

- A Docker-mode framework stack serving production on `localhost` comes up: the
  Caddy `frontend` script no longer opens with a blank line, which the operator
  re-saved as an unparsable `|4-` block once it had cut the `src` workspace
  (`Stack prepare: yaml: … did not find expected '-' indicator`).

- The shared Celery worker runs a bounded prefork pool: `celery_concurrency`
  (default 2) in `stacks/dev`, `stacks/local` and `stacks/staging`. It ran
  Celery's default of one child per CPU, each loading the whole application,
  which held 10 GiB on a 32-core staging host. The queue worker keeps its own
  thread pool.

- A framework stack serving production (`stacks/dev` with `serve_mode:
  production`, `stacks/staging`) installs its slots editable, so
  `frontend-build` reads each fragment from its slot. A non-editable install
  copied the addons into site-packages, where the fragments' `tsconfig.json`
  chain (`../../tsconfig.base.json`) and their pnpm dependencies do not exist,
  and the build failed with `Tsconfig not found`. `stacks/local` still installs
  non-editable; its frontend builds from the baked web image. An existing stack
  drops `--no-editable` from its `py-sync` command and reruns the chain.

- A change to `ANGEE_UI` alone now recomposes. `angee.web` renders it into
  `runtime/web/manifest.json` but never declared it a compiler input, so
  `compose --if-stale`, the stack's compose job, found the receipt current and
  kept the old deployment layer while `compose --check` reported it stale. The
  web autoconfig appends `ANGEE_UI` to `COMPOSE_FINGERPRINT_SETTINGS`. Like
  every structural input, `ANGEE_UI` is authored YAML: a deployment that set it
  through `ANGEE_UI` or `YAMLCONF_ANGEE_UI` in the environment is refused and
  moves it to its `settings.yaml`. GraphQL had the same gap for the SDL: its
  autoconfig now appends `STRAWBERRY_DJANGO` and `ANGEE_GRAPHQL_RESOURCE_FILTERS`,
  so a host override of either alone recomposes, and neither comes from the
  environment.

- The sign-in card's "or use password" divider shows only beside a sign-in method
  that renders. A contributed method that renders nothing, such as the OIDC
  method with no configured connection, no longer leaves an orphan divider above
  the password form.

- The Django images run as the owner of the mounted project, so a Docker-mode
  stack works on a Linux host whose stack root does not belong to uid 1000. The
  entrypoint gives its `angee` user the project's uid and gid before dropping
  privileges, as the operator image already does; a root-owned project keeps the
  image's own user. Before, `py-sync` failed with `Permission denied` creating
  `caches/uv`, and runtime files were left owned by uid 1000.

- A Docker-mode framework stack (`stacks/dev` with `runtime_mode: docker`,
  `stacks/staging`) installs its JS dependencies with the framework's pnpm. The
  stack root's `package.json` now declares `packageManager: pnpm@11.1.3`, the
  framework's own. Without it Corepack in the `deps` container downloaded the
  latest pnpm, and pnpm 12 refused the framework's patched dependencies
  (`ERR_PNPM_INVALID_PATCH` on `nuqs-2.10.1.patch`). An existing stack adds the
  line to its root `package.json`, or re-renders with `stack update --template`.

- Permission statements are compiled for the stored grants their actor can hold
  (django-zed-rebac 0.35.2, actor grant profiles). Inside an evaluator scope one small
  statement reads which stored relations name the actor, directly, by wildcard or
  through stored subject sets, and arms the actor holds no row for are left out, in
  granting positions only. On PostgreSQL an "administrator holds this on every row" arm
  that is left out keeps `(SELECT FALSE)` in its place, so each id set is still planned
  at its table's size and broad reads keep their hash joins. On the composed framework
  host a member's selected-message read went from 1,618 KB of SQL and 1,217 MiB of
  planner memory to 523 KB and 694 MiB with the same rows. Hosts set nothing:
  `REBAC_ACTOR_PROFILES` is on by default, and `False` restores the earlier statements.
  What a host can observe:
  - Statement totals move inside a scope. A read whose actor holds a fixed role (an
    administrator) issues one more statement, the profile's; a read whose actor does not
    issues one fewer on the console documents, because the constant arms it cannot hold
    need no witness. A denied point check issues the profile's statement again before
    it answers.
  - After a grant is written, the next read in that scope reads the profile again.
    While a transaction holds uncommitted grant writes the profile is read at each
    compile until that transaction ends, and a queryset read compiles twice.
  - A grant of a kind the actor did not hold, written by another process, may be missing
    from reads until the scope ends or a check in it refreshes the profile. It is never
    the other way round: a revoked grant is evaluated live.
  - A workflow step body now reads inside one evaluator scope
    (`WorkflowRunner._run_body`), as a request and a GraphQL operation already do, so
    workflow steps get the same statements. Work that runs outside any scope (a
    management command, a task that opens none) is compiled as before.
  - Every changeset writes one stored grant naming its actor, so an active user holds
    one grant row per write boundary. 0.35.2 is the floor because its profile read does
    not grow with those rows: a member with 100,000 of them reads the projects list in
    79 ms where a member with none takes 71 ms, and 0.35.1 took 315 ms. Such a user's
    scope issues two profile statements instead of one, and a relation it holds is
    kept on every type that declares that relation name.

- A theme or branding fragment owns the sign-in presentation through
  `auth.login#hero`, a framework container with one child per host. Installing
  the fragment is enough: `LoginPage` renders the hero on `/login` and
  `/sign-in-link`, so a host keeps the template's `main.tsx` that mounts
  `IamLoginPage`, and a rendered host needs no re-render or new answer. The
  fragment declares:

  ```tsx
  containers: {
    "auth.login#hero": {
      "theme.acme.login-hero": {
        content: { render: AcmeLoginHero, presentation: "full-bleed", backgroundImageUrl: acmeBackdropUrl },
      },
    },
  },
  ```

  An explicit `hero` prop still wins. The hero renders at every width, stacked
  above the card on small screens; it picks its own responsive assets through CSS
  or media queries, with no viewport hint from the page. `presentation:
  "full-bleed"` (also a `LoginPage` prop) puts the backdrop and the floating card
  behind any hero, so a branded host reaches the layout that only the unbranded
  framework hero had. A host's hero is no longer hidden below `lg`. A contributed
  hero brings its own `backgroundImageUrl`; the page's prop serves only the
  page's own hero, so IAM's rotating backdrops stay with the framework hero. Two
  installed contributors fail at boot; keep one by `remove: true` on the other
  from the deployment's `ANGEE_UI.containers`.

  **Host upgrade:** a host that hand-edited `main.tsx` to mount its own branded
  page restores the template's `host-auth` route on `IamLoginPage` (revert the
  edit, or `stack update --template`), drops the fragment's package from its own
  `package.json` and relocks at the stack root; the fragment declares the hero
  as above. A kept hand edit still works, since its explicit `hero` wins.

- The `auth.login` route's declaration is the one spelling of the sign-in URL.
  `createApp` reads its path for the auth gates, the auth provider and
  `useLoginPath`, and its `loginPath` input is removed: a host that passed it
  drops it and declares `auth.login` at that path. `DEFAULT_LOGIN_PATH` remains
  only for a runtime that composes no `auth.login` route.

- `@angee/ui`: `CommandBand.editable` keeps an editable record's command band
  row in the layout while nothing in it renders, blank and hidden from assistive
  technology, so the Save and Discard its first edit brings shift nothing.
  `FormView` sets it from the save surface's `formTakesEdits` (`formReadOnly`
  without the wait for the record), so the row holds from load on. The layout
  band marks the band holding its row `data-layout-band`; an inline band (the file
  details card, a relation record dialog) collapses as before, as does a record
  that takes no edits and has no verbs.
- `@angee/ui`: every call `MutationDialog` makes for its caller (`validate`,
  `onSubmit`, `mutation`) gets the `AuthoredMutateOptions` that turn refine's
  generic error notification off, so a failure is reported once, in the dialog.
  `@angee/refine`'s `ActionMutate` takes them as a third argument; the dashboard
  store's `save` and `createPersonal`, the operator's `OperatorActionHook.run` and
  `useDaemonMutationRun` take them per call. The dashboard, portfolio-update and
  add-source dialogs run their owner's call as `mutation` (deleting their
  `onSubmit` result wrappers); the operator's workspace-create and secret dialogs
  pass the options on from `validate` and `onSubmit`.
- `@angee/ui`: an action's `submit(values, context, options)` gets the same
  `AuthoredMutateOptions` from the surface running it: `useActionForm` (so
  `ActionFormDialog` and the stream composers) renders a failure in its form, and
  a row's direct submit settles it in one toast. The framework's action submits
  pass them on to their authored call, and `useRecordChromeActionOutcome`'s
  authored transport forwards them, so a transport failure is reported once.
- `angee.integrate`: `Integration.credential_blocker(credential)` is a
  capability's one admission rule, a public-safe refusal or `None`;
  `admit_credential` raises it on every attachment and the connect picker shows
  it. `ConnectWithAccount.accounts` lists `ConnectWithAccountChoice` entries (`id`,
  `display_name`, `blocked_reason`), so the connect-with-account field lists an
  account the capability refuses with its reason ("Already connected" for a Google
  account another calendar source syncs) and never offers it, instead of refusing
  it once picked. `Mount`, `CalendarSource` and the calendar backends move their
  rules onto `credential_blocker`; `GoogleCalendarBackend.admit_credential` only
  holds the credential's row lock around it.
- `@angee/ui`: `PaneRow` takes `disabled` and `disabledReason`. An unavailable row
  stays listed, muted and without a hover fill; it is a focusable button with
  `aria-disabled`, so keyboard users reach it, and its reason, shown in full under
  the title, is its `aria-describedby` description. The connect-with-account
  field lists a refused account through it.
- Calendar time zones follow Google Calendar's model. An all-day event is dates
  in its effective zone, `CalendarEvent.effective_timezone` (its own, else its
  calendar's `Calendar.effective_timezone`, else the project `TIME_ZONE`; it
  replaces `expansion_zone`, which left the project zone blank; the
  `effective_timezone_expression()` of `CalendarEventQuerySet` and
  `CalendarQuerySet` reads it as a query), read as `effective_timezone` on an
  event and on each `event_occurrences` item: the calendar grid, the events list
  and the event form show and edit those days in every browser zone, and
  changing an all-day event's own zone keeps its days. The events list shows a
  timed event's bounds as their date and clock time in the viewer's zone.
  `@angee/ui`'s `AbsoluteTime` reads a bare `yyyy-MM-dd` value as that date,
  with no time and no zone, and list date cells compose it. Dragging an all-day
  event sends the days it lands on, which the event places at midnight in its
  zone; the nearest-midnight snap is gone, and an instant off that midnight is
  refused. A dragged timed event refuses a time without an offset. A
  timed event is an instant edited on the viewer's clock: `@angee/ui`'s
  `formatDateTimeStorage` (`DATETIME_STORAGE_FORMAT`) writes the local wall time
  with its UTC offset (`yyyy-MM-dd'T'HH:mm:ssxxx`), so every `datetime` widget
  (record forms, filters, structured form specs, action dialogs) sends the
  instant meant, never a naive time the server reads in its own zone, and
  `mutationDialogValueCodecs.datetime` composes it. `calendarDay(date,
  timeZone)` reads the day an instant falls on in a zone. A backend asks a parsed
  event's zone of the same chain (`angee.calendar.backends.effective_timezone`):
  Google Calendar reads and publishes every zone through it and the two
  properties, and a published event's version names the zone its projection
  reads.
- A calendar source can publish local calendars out. `CalendarBackend.direction`
  (`PULL` by default) declares which way a source's streams sync; a `PUSH`
  backend's calendars and events streams are push streams, never swept.
  `CalendarSource.publish(calendar, remote_id)` and the `publish_calendar`
  mutation (`write` on the source, `delete` on the calendar) bind an existing
  local calendar to a remote one through the source's `calendars` link, refusing
  a calendar another source mirrors or publishes; `ANGEE_CALENDAR_PUBLISH_TARGETS`
  (`None`: any) bounds the remote calendars a deployment publishes to, checked
  again before every remote call. A publishing source's purge unbinds its
  calendars and keeps their events and its key. `angee.calendar_integrate_google`
  adds the `google_publishing` backend, which publishes to any Google calendar
  shared with its key: a `jwt_bearer` key minted for `calendar.events` (no
  delegation) and `connect_google_calendar_publishing(credential_id)`.
  It creates each master or single event once under an id derived from its
  public id, stamped `extendedProperties.private.angee_event` and looked up by
  that stamp before any insert (a `409` adopts the event when the stamp and the
  sent fields match), maps a series to its `RRULE` and each override to its
  instance (a cancelled override cancels it), and writes every later change under
  `If-Match`: Google's `412` is a held conflict, never an overwrite.
  `GoogleCalendarClient` gains `stamped`, `insert`, `patch` and `delete`.
- Push streams propagate deletes and accept a kept remote. A push link holds
  nothing (`RecordLink.live_reference_filter`): a row only push links name is
  deletable, its deletion releases them (`RecordLink.teardown_for_target`,
  `RecordLinkManager.release_pushes`), and `push_stream` writes the removal of
  each released, written link as `write_back(link, None)` before tombstoning it;
  a link still naming a row a delete that locked nothing removed counts as
  released (`RecordLinkQuerySet.released`, one anti-join per target type).
  A row a pull or two-way link names stays protected, and a published calendar
  is refused deletion until it is unpublished. Keeping the remote of a push
  stream's conflict (`RecordLink.resolve_conflict`) records the remote's current
  version as the link's base over the local projection, copies nothing, and
  pushes again only after the next local change. `push_stream` reads the cycle's
  deadline (`sync_deadline()`) between candidates. `push_candidates` is public.
- Base's target-delete receiver filters, then tears down: an edge that
  `blocks_target_delete` has the rows naming the target read once, those its
  `live_reference_filter` selects refuse with `ProtectedError` (the rule creation
  undo applies), and its teardown or policy runs only while a row names the
  target; a teardown never blocks. Its read of an edge's rows loads only their
  keys. `RecordLink`'s teardown only releases push links
  (`RecordLinkManager.release_pushes` no longer refuses; its caller holds the
  row's lock inside the deleting or moving transaction, and it locks the row
  again before its links). `RecordLinkManager.lock_target` locks a linked row in
  its model's delete order (`AngeeModel.lock_for_delete`), read through its base
  manager. `push_stream` skips a link released since its batch read it, and binds
  a written candidate's row only when, under the row's lock, the adapter's keyed
  `local_changes` still yields it under that key on a push stream (a push
  adapter's keyed read yields each selected row, changed or not:
  `BridgeImpl.local_changes`); a row deleted or moved off its key
  meanwhile (a moved override, new or written before) binds nothing, so the next
  push writes the removal. `CalendarEvent.rebase_overrides` locks the overrides
  before moving or deleting any. `SyncStreamQuerySet.in_lock_order` is
  integrate's stream lock order (roots before contained streams), composed by
  `retire_children` and the calendar purge; a contained stream cannot hold
  containers (`SyncStreamManager.current`). A missing seeded row an
  implementation default names raises `ImproperlyConfigured` with the
  `manage.py resources load` guidance (was `ValueError`).
  `angee.base.querysets.row_lock_queryset(model, *, no_key=False)` is the one
  base-manager row lock (each row it reads and its MTI tables): `lock_subject`,
  `lock_target`, storage's and knowledge's target locks and messaging's thread
  teardown compose it.
- A published calendar's poll projects only the events changed since their push.
  - **Provenance:** `ModelHistory.current_version(model)` is the outer row's latest
    history version id, joined with the latest id of each tracked MTI descendant's
    table that carries its columns; it is opaque, compared only for equality. It is
    exact and committed with the write, and `NULL` before the row's first version or
    while capture is off.
  - **Local versions:** `LocalChange.local_version` (and `RecordChange`'s) carries the
    version a push adapter read with its projection. The push records it beside the
    base it writes: `RecordLink.local_version`, a new column the link's history
    excludes, framed by the mapping version. Every other promotion clears it.
  - **Narrowing:** `RecordLinkManager.with_local_versions(rows, context=…)` annotates a
    row's history version under a container setting, folded into a digest; a version
    is at most `LOCAL_VERSION_LENGTH`, and the push refuses a longer one before it
    writes. `RecordLinkManager.changed_since_base(stream, rows)` leaves out each row
    a link holds at that version.
  - **Sweep:** while `SyncStream.sweep_due` (once per `reconcile_interval`) the read
    narrows nothing, so a projection change without a mapping version bump, or a
    write that skipped history capture, still converges. A push stream's push
    completes its sweep (`last_reconciled_at`). A publishing calendar backend
    declares an hourly one (`CalendarBackend.reconcile_interval`), so the polls
    between stay narrowed. A stream keeps the interval it was first opened with
    (`SyncStreamManager.current` seeds policy once), so streams opened before keep
    the poll interval.
  - **Settling:** `push_candidates(settle=True)` (the push) records the newer versions
    of rows whose projection still equals their base in one statement, conditionally
    on each base, and baselines a row read without a version
    (`RecordLinkManager.settle`, `ModelHistory.record_missing_baselines`).
  - **Google:** `local_changes` versions each event under the calendar's zone, so a
    zone change re-reads every event without touching one. An edit committing
    after the push read its row is the next push's. The publishing backend declares
    its events stream's mapping version (`CalendarBackend.events_mapping_version`),
    and a test pins what a push writes to it.
  - Hosts add the `RecordLink.local_version` column (`makemigrations`). Existing
    links start unknown, so the first poll reads every published event once. Its
    database default is that unknown version (`CharField(blank=True,
    db_default="", default="", max_length=255)`), so a release before the column,
    rolled back onto the migrated database, still creates links. A host that
    already added the column gets one `AlterField` for the default.
- **Breaking (`delete_blocker` overrides):** `AngeeModel.delete_blocker(*,
  cascaded=False)` says where the row sits in the delete: `DeletePreview` judges
  its target as the start and every collected row as cascaded, and a model's
  `pre_delete` receiver enforces the hook's rule for where each row sits,
  passing `cascaded=not starts_delete(instance, origin)`
  (`angee.base.signals.starts_delete`, which the preview composes too). Every
  override accepts `cascaded` (pass it to `super()`). A mirrored calendar
  refuses only a delete started at it, so its source's delete preview no longer
  shows that refusal; a published calendar refuses wherever it sits; an event or
  attendee a delete only cascades to goes with the row it started from. Judging a
  calendar reads only its source's backend key (`Calendar.source_backend_key`,
  `CalendarSource.backend_class_for`). `publish` takes a no-key lock on the
  vendor. Google's `require_calendar` probes with `events.list` (one event, the
  `kind` field), which the publishing key's `calendar.events` scope allows. A
  removal whose `412` finds a master or single event cancelled or gone, or an
  override's instance gone or holding exactly the restore, is done; an instance
  Google cancelled meanwhile is a held conflict. Every mapping error of one
  synced event, its `observe` included, is that event's quarantine, and a
  master Angee cannot map keeps its linked instances listed under its refusal
  (`CalendarBackend.quarantine_series_instances`, one read of the stream's
  instance keys), so no absence sweep or identity read withdraws its cancelled
  instances; an instance Google serves on its own keeps its own payload in both.
- A publishing calendar backend implements `write_event` and `read_written`;
  `PublishingCalendarBackend.write_back` and `read_keys` run them after `admit_push`, which
  refuses an unsubscribed source and a remote calendar
  `ANGEE_CALENDAR_PUBLISH_TARGETS` does not list, once per push as the stream's
  failure. The setting is matched whole and checked at startup (`calendar.E001`).
  A publishing purge moves the source's streams to a new generation before
  unbinding, so a push already writing binds nothing. `publish` reads the
  lifecycle on the locked source, locks the vendor, the stream, then the
  calendar, and refuses a remote calendar another source publishes to. An
  override only pushed is rebased with its series, and its push restores the old
  instance. `Credential.delegated` names a `jwt_bearer` key with a `subject`;
  the Google publishing backend refuses one. Google's refusal of one event
  (`400`, or a `403` naming the event) is `EventRefused`, that event's own
  `RemoteRejected("event_refused")`; a `404` reads as a gone event only once the
  calendar is known to be served, else it is the calendar's failure.
- Calendar sources dispatch on their backend. `CalendarBackend.direction` is the
  one fact of whether a source's calendars take local edits (`writable` is
  deleted; `CalendarSource.is_backend_writable` reads `direction != PULL`); a
  publishing backend extends `PublishingCalendarBackend`, which owns
  `admit_push`, `write_back`, `read_keys` and its purge (`release`: unbind),
  while `CalendarBackend.release` deletes a mirror's events and archives its
  calendars. `CalendarSource.unsubscribe` returns the backend's `unsubscribed`
  message, and `Calendar.delete_blocker` its `calendar_delete_refusal` (the
  `SOURCE_CALENDAR_DELETE` and `PUBLISHED_CALENDAR_DELETE` constants are
  deleted). A purge locks every stream of the source. A publishing backend's
  streams sweep hourly (`CalendarBackend.reconcile_interval`; a pulled one's,
  once per poll interval), and integrate's absence pass never sweeps them. The Google
  publishing backend is `google_publishing` (`GooglePublishingCalendarBackend`,
  `connect_google_calendar_publishing(credential_id)`; formerly
  `google_resource`). A publishing source holds no `statement_source` on the
  calendars it publishes (calendar REBAC revision 5). `event_columns(event,
  fields)` is calendar's public column projection, which Google's push
  projection composes with `canonical_rule`; `CalendarEvent.span_end` gives an
  occurrence's end without a row; `GoogleCalendarClient.instance` takes the
  original start as a moment and owns its wire form. Given keys, Google's
  `local_changes` reads only the events they name, and no projection reads
  attendees.
- `angee.integrate`: `IntegrationManager.build(**fields)` returns the unsaved
  capability row `create_capability` saves; `Credential.grants(scopes)`,
  `CredentialQuerySet.owned_by(user)` and `Integration.ready_oauth_client()` are
  the one rule each (`ConnectWithAccount.of` is a pure projection). A Mount's
  vendor is its backend's `defaults` (`MountBackend.vendor_slug` is deleted).
  `@angee/integrate` adds `useConnectWithAccountQuery`, which Mount's and
  Google Calendar's connect actions compose.
- One connect-with-account surface in `angee.integrate`: `Integration.reads_with`
  (an OAuth grant of the capability's client covering its `oauth_scopes`) and
  `Integration.connectable_credentials(user)` replace `Mount.reads_with`'s rule,
  `MountManager.connectable_credentials` and `MountManager.connect_oauth_client`;
  a capability offers accounts before it exists from an unsaved row
  (`IntegrationManager.build`, which `create_capability`, `CalendarSourceManager.subscribe`
  and `MountManager.build` compose). The `ConnectWithAccount` GraphQL interface
  (`oauth_client`, `oauth_scopes`, `accounts`) is implemented by `MountBackendType`
  and the new `calendar_source_backend(backend_class)` query's type; integrate's
  console `types` bucket registers it, so a host composing neither still has it.
  `@angee/integrate` exports `ConnectWithAccountAction` and
  `useConnectWithAccountField` and owns the `ConnectWithAccountFields` fragment
  their consumers spread. The action takes the consumer's authored connect
  mutation (`connect`, over `{ credentialId }`) and its dialog runs it as
  `MutationDialog`'s `mutation`, so a refusal is reported once. While the
  capability has no ready OAuth client and the user no account it reads with, the
  account field says the connection is not configured and links an administrator
  to the OAuth providers page, instead of an empty list and a disabled Connect. `ConnectMountAction` composes the field
  (`MountAccountPicker` is deleted; its `mount.account.*` copy moves to the
  `integrate` namespace), and `@angee/calendar-integrate-google` adds "Connect
  Google Calendar" to the sources toolbar over `connect_google_calendar(credential_id)`.
  `GoogleCalendarBackend.admit_credential` composes `reads_with` and keeps only
  one source per account.
- `angee.calendar_integrate_google` syncs a connected Google account's calendars
  (the `google` client, `calendar.readonly`) with sync tokens;
  `connect_google_calendar(credential_id)` connects one of the user's accounts as
  one source, and `GoogleCalendarBackend.admit_credential` refuses an account
  another source already syncs, on every attachment. Every calendar connect verb
  runs `CalendarSourceManager.subscribe` (quota and rate under the owner's row
  lock, integrate's attachment under the credential's). A source's events stream
  is held by its calendar's link (`SyncStream.container`);
  `CalendarSource.calendar_for_partition` is removed and `events_stream` takes the
  calendar's link. A calendar the source stops listing retires its events stream
  and withdraws its events; when it is listed again they re-apply. Each sweep
  lists a calendar once, in pages of 250, and an empty calendar list confirms no
  absence. `CalendarSource.admit_credential` delegates to
  `CalendarBackend.admit_credential`.
- `angee.integrate`: `BridgeImpl.on_absent` may return each withdrawal's
  `ApplyResult` by link; the driver records it as the link's applied base, as a
  page tombstone, so a record that returns applies. A CardDAV address book that
  returns restates its folder's name.
- `angee.calendar.backends` owns the source-neutral readers both calendar
  backends compose: `series_rule` (a VEVENT's first `RRULE`) and `excluded_dates`
  (through icalendar, now an `angee-calendar` dependency), `cancelled_instances`
  (each `EXDATE` judged by `CalendarEvent.is_instance`), `organizer_first` and
  `parse_key`.
- A fresh installation no longer offers "Continue with Google Gemini" on the
  sign-in page. `iam_integrate_oidc`'s install seed gave Google's public Gemini
  CLI client `login_enabled: true`, and that client ships with a published client
  id, so every new database showed it. The seed now contributes the client's OIDC
  metadata only; signing in through it is an operator's switch on the OAuth
  client. An installation that already loaded the row keeps its current value:
  turn it off on the client if it was never chosen.

- A host whose name opens one app still reaches the full console. An
  `ANGEE_UI.shell.apps` entry without a `rail` shows every root, with its own
  brand and theme else the shell's, so `apps: {console: {}}` makes
  `?app=console` the full menu on any host. `ANGEE_UI.shell.app` names the app
  a page opens when neither `?app=` nor its `hosts` entry selects one, so a
  product's settings can open its app without knowing the deployment's host
  names. Both are checked at boot like `hosts`.

- `stacks/staging` renders a remote, public, container host in one command from
  repositories, with no project repository carried to the machine:
  `angee init <dir> -t staging --repository <repo> --extra-source <repo>`. It is
  `stacks/dev`'s Docker mode with production serving fixed, no Storybook or
  Playwright, a first administrator and a `makemigrations` job that authors the
  host's own migration history, so its data is disposable. A repository given by
  SSH URL is fetched with its own deploy key, the required secret
  `deploy-key-<name>`; `settings_file` names a repository's settings file, loaded
  as the final `YAMLCONF_CONFFILE` layer. It needs angee 0.28.0 or later.
  [Templates](templates/README.md#deploy-a-staging-host-in-one-command).
- Stack templates read three wrapper-set flags instead of the flavor:
  `bootstrap_admin`, `dev_tools` and `author_history`; `template.active` comes
  from the wrapper's `stack_template`. `stacks/dev` and `stacks/local` render the
  same manifests as before, with two exceptions that apply to both. `py-sync`
  authors a missing `uv.lock` before its locked sync, so a freshly rendered host
  no longer stops there. A production render behind a public `ingress_domain`
  sets `YAMLCONF_DEBUG` to false and `YAMLCONF_ALLOWED_HOSTS` to the domain, its
  aliases and the compose-internal names; it served the project's `DEBUG: true`
  and wildcard hosts before.
- `projects/web` takes `repositories` (a requirement and an editable source per
  repository slot, in a framework workspace only) and `roots` (root apps appended
  to `INSTALLED_APPS`). The `src` workspace template declares
  `_angee.source_slot`, so the operator cuts one slot per stack source marked
  `slot: true`.

- `storage/file#write_granted` holds only the fragments' record arms; `write`
  derives the drive, folder, inherited-owner, draft-owner and admin arms itself,
  and `owner_writer` is `inherited_owner + draft_owner + (owner & write_granted)`.
  Every `read`, `write`, `share`, `delete` and `transfer` decision is unchanged;
  a file's read statement no longer compiles its folder's write, which `delete`
  reached through `owner_writer`. Fragments contribute `write_granted` arms as
  before. `write_granted` and `owner_writer` are composition terms: a caller that
  checked either directly checks `write`, `delete` or `transfer`. Operators re-sync
  the permission schema: storage revision 14.

- An `angee.integrate` stream names its container: `StreamDefinition.container`,
  persisted as `SyncStream.container` (a `PROTECT` key to `integrate.RecordLink`,
  `related_name="child_streams"`), is the parent stream's link whose record
  holds the partition; the partition is its external key. The container is
  immutable on the model; a stream declared before containers existed adopts it
  once. The parent declares `holds_containers` (`SyncStream.holds_containers`).
  `SyncStream.partition` widens to 512 characters: a child's partition is its
  container's external key, so both take `integrate.states.EXTERNAL_KEY_LENGTH`;
  the driver refuses a longer replica identity once, as a coalesced stream
  failure, and the page goes on.
  `RecordLinkQuerySet.observing_rule()` and `live_container_rule()` (observing,
  bound to a target, no absence counted) are the one rule for joined reads,
  `observing()`, `live_containers()` and `containers_of(bridge, key)` apply it.
  A container missing from a complete listing (`StreamPage.absent`) counts
  against the parent's absence threshold: an unavailable container pauses its
  child, which is neither declared nor retired; a container that stops observing
  (tombstoned or withdrawn) retires it. Retirement flips the child to
  `StreamState.RETIRED` inside the parent's page
  (`SyncStreamManager.retire_children`); a bounded, resumable pass over
  `reconcile_state` then tombstones the child's links and closes their open
  failures (`RecordLinkManager.mark_absent(..., confirmed=True)`) with
  `on_absent`, nothing is deleted, and the pass pauses while the container
  observes again (reopening drops it). `mark_absent` now returns the links whose
  status changed.
  `bump_generation` reopens a retired generation only while its container is
  live, and `reset_stream` reports a retired one exhausted. `sync_bridge` drains
  every declared partition in its first wave, a container-held partition only
  after the stream holding its container applied its pages and sweep (in holder
  depth order, or concurrently once that worker finished), runs the holders'
  retirement passes last, declares again only after a container-holding stream
  gained a live container, and opens no partition and starts no wave after its
  deadline. A cyclic or self-held container declaration fails fast. New
  `RecordLinkQuerySet.observing()` and `SyncStreamQuerySet.latest_generations()`.
  A page of a superseded generation is refused as a "superseded stream generation".
  `ImmutableFieldsMixin` checks only the immutable fields a partial save's
  `update_fields` writes.
- `angee.parties` address books are records of the directory's `addressbooks`
  stream: each book's link targets its `Folder` (`FolderManager.ingest_addressbook`,
  which truncates an over-long name) and holds its contacts stream; both
  declarations live on `DirectoryBackend` (`addressbooks_stream`,
  `contacts_stream`). The folder's name is an `identity` statement, so a person's
  rename outranks a remote one; `Folder` composes statements and history, and
  `parties/folder#write_statements` admits the directory (parties revision 20).
  A book missing from one listing pauses its contacts; once its absence is
  confirmed it is tombstoned, its statements are withdrawn (as a read tombstone
  withdraws them) and its contacts stream retires. An empty listing confirms
  nothing. CardDAV discovery raises when the server refuses the principal or
  home-set request instead of falling back to the server URL.
  Removed: `Folder.source_href` and `uq_folder_directory_source`,
  `FolderManager.sync_addressbook`, `Directory.addressbook_stream`, GraphQL
  `ContactFolderType.source_href`, and the `folder` argument of
  `PartyManager.ingest_contact`, which now takes the page's `stream` and finds
  its folder through that stream's container. `StatedRecordMixin` moves from
  `angee.projects.models` (which still imports it) to `angee.integrate.mixins`,
  beside `LinkedRecordMixin`, the `record_links` relation `Party` composes;
  `Folder` composes `StatedRecordMixin`. A source backend implements `discover`,
  `extract_contacts` and `read_contacts`.
  Upgrade: a host runs `makemigrations integrate` for the `container` and
  `holds_containers` columns and the widened `partition`, copies the migration template
  `src/angee/parties/compose_migrations/parties/addressbook_links.py` into
  `runtime/parties/migrations/` and pins its placeholder dependencies to its own
  leaves, then runs `makemigrations` for the folder's history and statement
  tables, `migrate` and `rebac sync`. Every folder, contact and link is kept and
  nothing is downloaded again. A folder whose href is longer than a link's
  external key never synced; the template skips it and logs a warning. The first
  sync states each listed book's name on its folder, so a folder renamed after
  the last sync before the upgrade takes the directory's name. A book removed
  before the upgrade misses its first listing then: its contacts pause in that
  sync, unopened, and retire on the sync that confirms its absence (the second,
  at the default threshold of two). The template reverses by restoring each
  folder's `source_href`. Upgrade cutoff: the template ships through the 3.x
  releases and is removed in 4.0; a host upgrading from an earlier release
  applies it through a 3.x release first.
  The parties web fragment lists the folders on a read-only Folders page
  (`parties.folders`, `/parties/directories/folders`). Its record shows the name
  and directory; field provenance, the Links tab's Sources and History compose
  onto it from their owners. The `parties.directories` menu node becomes a
  route-less Settings category holding `parties.directories.list` (the
  directories) and `parties.folders`.

- A resource row naming a `to_field` foreign key by xref stores the named row's
  target column, not its primary key: `AngeeResource.get_fk_widget` binds the xref
  widget's native `field` to the relation's `target_field`, so a seed can set
  `projects.Task.activity_type` (keyed by `ActivityType.key`), which previously
  failed validation, and export renders the key back. An adoption condition
  compares a related row by the same column.

- Time-of-day fields can be exposed as resources. Resource metadata accepts the
  GraphQL `Time` scalar, which it previously refused at schema build, and a Django
  `TimeField` defaults to the new `time` widget. It filters with Date's operators
  (`exact`, `ne`, `gt`, `gte`, `lt`, `lte`, `inList`, `notInList`, `isNull`), sorts,
  and groups by value without calendar extractions. On the web, `Time` is a
  generated `string` (`HH:MM[:SS]`, no zone); `@angee/ui` registers the `time`
  widget, which edits through the native time control and reads and lists through
  `formatTime`. `formatTime` and `formatTimeInput` accept a `Time` value and read
  it as written, an empty time drafts as `null`, the filter editor offers a `time`
  field type, and the client row model compares clocks as SQL does (`09:30` equals
  `09:30:00`).

- Storage mounts read Google Drive: `angee.storage_integrate_google_drive` mounts
  My Drive, a folder or a shared drive, in place (read-only) or copied into a chosen
  writable backend, reading with one of the connecting user's Google accounts that
  granted `drive.readonly`; Google-native documents and shortcuts are skipped and
  counted. One `connect_mount(backend_class, name, source_root, mode, credential_id,
  copy_backend_id)` mutation replaces `connect_local_folder` and the per-backend
  connect mutations, and `mount_backend(backend_class)` tells the dialog what a
  backend asks for. A mount's root is its backend's source token
  (`MountBackend.source_root`), `MountEntry.storage_key` keeps a file's row across
  renames and moves, `Backend.writable` and `ExternalStorageBackend` own read-only
  serving, and a refused download answers 404, 403 or 502 instead of 500.
- Integration follow-ups across the OAuth, credential and mount owners:
  - every token request (the authorization-code and refresh grants, the JSON shim and
    the JWT bearer assertion grant, now `angee.integrate.oauth.client.jwt_bearer_grant`)
    maps its failures once and keeps the response's real HTTP status, so a JSON 429 on
    an OAuth refresh is transient instead of failing the credential; a token endpoint
    the address policy refuses is the permanent `endpoint_not_allowed` refusal;
  - `CredentialKindHandler.carry_forward` decides what a new grant keeps of a
    credential's stored material (the OAuth kind keeps its refresh token);
  - a code exchange whose response omits `scope` granted the requested scopes
    (RFC 6749 §5.1), so the credential records them;
  - `connect_account_start(scopes:)` requests a consumer's scopes, `@angee/integrate`
    exports `ConnectAccountButton` and `useConnectAccountStart`, and the Drive account
    picker connects another account with `drive.readonly`;
  - `Integration.admit_credential` is asked on every attachment; a Mount admits only
    an account its backend reads with (`Mount.reads_with`);
  - `angee.integrate.http.retry_after` is the one `Retry-After` parser (delay-seconds
    or a future HTTP-date, at most a day); posts, Notion and Google Drive compose it,
    and posts keeps its one-minute fallback;
  - the Anthropic, work, uom and WhatsApp migration templates carry named placeholder
    dependencies and no `applies()`; the Anthropic rehome gains its reverse, `restore`.
- `angee.calendar` is the framework calendar, moved from arpee's `arp.calendar`
  and reshaped: a `Calendar` belongs to a `parties.Party` and is the access root
  of its events, an exception to a recurring event is an override row (`series`,
  `original_start`), attendees are `parties.Handle` rows with the organizer as a
  role, calendars share as reader, editor or free/busy, and a `refuse` calendar
  rejects overlapping busy time. The event form offers the calendars the viewer
  writes, defaults to the only one, and offers a user with none their own personal
  calendar (`Calendar.objects.ensure_personal`), never creating one unasked. The
  [calendar README](src/angee/calendar/README.md) lists what it expresses.
  `angee.projects_calendar` overlays the viewer's due tasks on the calendar page;
  projects no longer depends on a calendar.
  `Recurrence.get_last_occurrence` returns where a series ends, `None` when any
  rule it holds is open-ended. Recurrence is bounded for every consumer: a `COUNT`
  over `MAX_OCCURRENCES` (10,000) is invalid and an expansion past it
  raises `ValidationError` instead of running on (`series_too_long`): a read is
  re-anchored at its window, like libical's, and a save counts a series from its
  start to a horizon; a rule is one bare `RRULE`, and one with no
  occurrence within about 30 years of its start is refused (`never_occurs`);
  `RecurrenceField(finest_frequency=…)` refuses finer rules (a calendar event
  repeats at most daily). A calendar read skips a row it cannot expand.
  A `refuse` calendar judges an event's overlap only from now, over two years
  and no further than the event ends, a series and a single event alike: an event
  wholly before now is never judged, and nothing is judged against its past. The
  check fails closed: a save beside another busy event it cannot expand is
  refused (`unexpandable`, naming that event) where a read skips it; a free one,
  or the saved event's own series, is skipped. `angee.integrate.sync` gains the
  sync's deadline seam, `sync_deadline()` and `sync_deadline_context(deadline)`:
  the bridge sync sets it once per cycle and every stream driver given a
  `deadline=` enters it, so an adapter's nested parse or identity read stops by
  it.
  **Host upgrade:** the `calendar` app label carries the deleted `arp.calendar`'s
  migration history and no data moves (clean slate). Each host writes a `calendar`
  migration (`0003`) that purges the old event, attendee and history rows and the
  `arp.calendar` resource-ledger rows, then applies the generated schema
  operations; then run `rebac --skip-checks sync --prune-stale --yes` (calendar
  revision 3, arp revision 7) and `resources load`. Add `angee.calendar` (and
  `angee.projects_calendar` where projects is composed) to `INSTALLED_APPS` in
  place of `arp.calendar`.
- `angee.calendar` mirrors connected calendars. A `CalendarSource` (an
  `integrate.Integration` child and a `Bridge`, its protocol selected by
  `backend_class` from `ANGEE_CALENDAR_SOURCE_BACKEND_CLASSES`) mirrors each remote
  calendar it serves through its `calendars` stream as a `Calendar` on its owner's
  person, whose `source` names it and whose name, colour and zone the source states
  (a person's own edit outranks it); each calendar's events sync through an
  `events` stream partitioned by its key. A backend declares `writable`: a
  read-only source's calendar refuses local edits on every save path
  (`Calendar.is_writable`), and `CalendarQuerySet.local_edits_rule()`
  (`taking_local_edits()`) is the same decision as a query, so the event form's
  `writable` calendars and `Calendar.objects.ensure_personal` take only calendars
  that accept local events. Its owner syncs it (`sync_calendar_source`) and
  unsubscribes it (`unsubscribe_calendar_source`): the source disconnects at once
  and the `calendar.purge_source` job deletes its events, archives its calendar and
  retires its credential. How many sources a user keeps, and how fast they add them,
  are bounded (`ANGEE_CALENDAR_SOURCES_PER_OWNER`, `ANGEE_CALENDAR_SUBSCRIBE_RATE`).
  The new `angee.calendar_integrate_ics` addon is the read-only `ics` backend: any
  signed-in user subscribes to a feed (`webcal://` reads as `https://`), whose
  address is the source credential's material; it is polled with a conditional GET
  through integrate's bounded download and parsed by python-icalendar (`VEVENT`,
  `VTIMEZONE`, `RRULE`, `EXDATE`, `RECURRENCE-ID`). It seeds the `ics` vendor in the
  master tier.
  **Host upgrade:** add `angee.calendar_integrate_ics` to `INSTALLED_APPS` beside
  `angee.calendar` and install its distribution (it requires `icalendar`); write the
  `calendar` migration with `makemigrations calendar` (`CalendarSource`, a calendar's
  `source` and `is_archived`, calendar statements and their history, and the event
  recurrence's daily floor; the ICS addon has no tables); then `migrate`,
  `rebac --skip-checks sync --prune-stale --yes` (calendar revision 4) and
  `resources load` (the `ics` vendor).
- One Google OAuth client per deployment serves every Google feature. The new
  `angee.integrate_google` addon owns the `google` provider preset and seeds the
  deployment's blank `google` client and Google's public `gemini` client
  ([Register the deployment's Google client](docs/howto/google-oauth-client.md)).
  `iam_integrate_oidc` depends on it and no longer ships `GoogleType` or the demo
  `google` row; it turns sign-in on for a `google` row the load creates, never for
  an existing one.
- A resource entry may declare `create_only` fields: they reach a row only when
  the load creates it (through any of its entries or an owner verb), never an
  adopted, existing or re-applied row, and they stay out of the row hash like
  transition-owned state. Adding it to a shipped entry re-applies that entry's
  existing rows once.
  `angee.resources.models.move_seed_ledger` moves seed ledger rows to the addon
  that now ships them in a data migration; the Anthropic and Gemini steps use it.
- Consumers declare the scopes they need (`IntegrationImpl.oauth_scopes`).
  Credentials are keyed by (user, OAuth client, external account), so one user
  connects several accounts through one client, and an integration stays bound to
  the account it was connected as (`account_switch_refused`). The integration
  resource's `account` is no longer updatable: the binding follows the credential,
  and only ownership transfer releases it, detaching the previous owner's
  credential and disconnecting the integration in the same transaction. When the
  provider can be asked for consent again (`OAuthProviderType.authorize_params`),
  a connect whose grant lacks the refresh token a refreshable client needs restarts
  asking for it (`consent_required`); one whose grant drops a scope an attached
  integration uses is refused (`scope_not_granted`). Retired provider keys
  (`OAuthProviderType.retired`) still load but are not offered for new clients.
- `integrate.OAuthClient` owns `issuer` and `jwks_uri`, and ID-token verification
  (with `ANGEE_OIDC_JWKS_TTL` and `pyjwt`) moved into `integrate`; the fake
  OpenID provider moved to `angee.integrate.testing.oidc`.
- **Upgrade.** Projects author the schema migration: the two credential constraints
  replace `uniq_integrate_credential_user_oauth_client`, and `issuer`/`jwks_uri`
  move to the base model (new columns where the login addon was absent). The
  reverse migration fails once any user holds two accounts at one client. For the
  data, copy the migration templates into `runtime/<app_label>/migrations/` and
  replace their placeholder dependencies with the host's own migrations:
  - `integrate_google/compose_migrations/integrate/align_google_clients.py` fills the
    preset's endpoints, catalogue and `include_granted_scopes` on existing
    `google`-type clients (the demo row included), never replacing client ids,
    secrets or values an operator set;
  - `integrate_google/compose_migrations/resources/rehome_gemini_seed.py` moves the
    `oauth_gemini` seed's ledger row from `integrate` to `integrate_google` (its
    content is unchanged), so the row is not re-applied;
  - bridges `posts_integrate_youtube/compose_migrations/integrate/move_youtube_client_to_google.py`,
    before `align_google_clients` (see its README).
- **Re-applied seed rows.** On the first provision after upgrading, the
  `oauth_google` seed adopts an existing `google`/`prod` client and sets its
  provider type, display name and icon; without the ledger step, the `oauth_gemini`
  row re-applies its released values. No other OAuth seed row changes.

- `MCPServer.authorization_for(agent)` owns the whole `Authorization` value an MCP
  server receives, for the in-process transport and the provisioned `.mcp.json`
  alike. Breaking for consumers and provisioned agents:
  - the container env var is renamed `ANGEE_MCP_BEARER_<sqid>` →
    `ANGEE_MCP_AUTHORIZATION_<sqid>` and now carries the whole header value
    (`Bearer …` or `Basic …`); `.mcp.json` renders `"Authorization": "${VAR}"`;
  - `Agent.mcp_bearer_env` is renamed `Agent.mcp_authorization_env`, and
    `MCPServer.config_entry(bearer_env=…)` takes `authorization_env=…`;
  - `MCPServer.bearer_for` mints only an internal server's per-agent bearer and
    refuses other placements, which receive `authorization_for`;
  - an external server sends its credential's own header: a basic-auth credential
    sends `Basic`, and a static token or OAuth sends `Bearer` (refreshed first);
    before, every kind's secret was sent as `Bearer`;
  - MCP credential kinds are restricted, and `MCPServer.clean` enforces it at save.
    External servers take a static token, OAuth or basic auth. An internal server
    takes only an active static token of at least 32 characters (stripped) that no
    other server, agent or integration references; an external server may not hold an
    internal key;
  - the internal HMAC now binds the server's sqid, an empty or weak key mints and
    verifies nothing (no `<sqid>.<HMAC("", sqid)>` forgery), and revoking the key cuts
    every agent bearer off.

  Reprovision every agent with MCP servers. Reprovision now re-renders the
  workspace's `.mcp.json` (`OperatorDaemon.update_workspace`, `PATCH /workspaces/{name}`)
  and refuses a locally modified one rather than overwriting it. The demo internal
  server's key is generated at load by the `agents.mcpserverkey` resource kind; the
  committed `demo-mcp-bearer-token` credential row is gone (an existing database keeps it
  orphaned until deleted).
- `integrate` gains the `jwt_bearer` credential kind: a pasted provider key file (a
  Google service-account key is one accepted vocabulary) minting per-scope access
  tokens through the RFC 7523 JWT bearer grant, via `Credential.auth_headers(scopes=…)`
  or `Credential.scoped_access_token(scopes)`. `CredentialKindHandler` gains `clean`,
  `probe`, `mint_access_token` and `records_failure`. A `jwt_bearer` credential's
  `secret_value()` is empty, so its signing key is never revealed or sent as a bearer.
  `Integration.probe_credential()` refuses any credential that needs reconnecting
  ("Reconnect this account.") before the kind's probe.

- Notion connections connect through Notion's public-integration OAuth from the
  saved record's **Connect** action; an internal integration's API token remains an
  alternative. `angee.knowledge_integrate_notion` installs a `notion` OAuth
  registration whose `notion` preset seeds Notion's endpoints, per-user `owner`,
  HTTP Basic client authentication and token-response identity; the deployment
  supplies the client id and secret. `OAuthClient` gains
  `token_endpoint_auth_method` (`client_secret_post` or `client_secret_basic`,
  honoured by both token-request formats) and `token_identity_keys` (the non-secret
  token-response keys connect reads the account from when userinfo cannot; token
  material is never among them). Projects author the `integrate` migration that
  adds both columns. The token-response keys were previously hard-coded as
  `account`/`organization` for every registration; Anthropic's install rows now
  declare them, and a resource reload updates existing rows.
- `connectIntegrationAction()` in `@angee/integrate` is the saved record's OAuth
  Connect child a vendor addon contributes at its own model's `#actions`; posts
  composes it, so `FeedConnectAction` and the `ConnectIntegration` re-export are
  removed.
- The Notion Markdown converter supports embeds, link previews and meeting notes,
  labels links that export no text by their kind, and removes the tabs Notion nests
  a heading's, paragraph's, quote's or container's children by (lists keep theirs;
  fences and tables keep their opener's). Toggle-heading children are no longer
  rendered as code, and pages carrying those blocks are no longer quarantined. The
  Notion mapping version advances, so the next sync re-reads every connection.

- `Message.objects.compose_reply` composes an email reply as a complete envelope:
  addressed to the parent's `Reply-To` when it carries one, else its sender (RFC
  5322 §3.6.2; `Message.reply_addresses(actor=)` is the one reader of `Reply-To`:
  lower-cased addresses with their display names, in header order, read with the
  caller's access, empty without the header), titled `Re: <thread title>` without stacking prefixes, and
  identified by a Message-ID (`make_msgid(domain=DNS_NAME)`, as Django mints one) as
  its external id. The email transport renders the parent's Message-ID as
  `In-Reply-To` and `References`, so the answer threads onto the same conversation
  instead of opening a new one. Replying to an email with neither a Reply-To nor a
  sender is refused; replies of other kinds are unchanged. Header parts named
  `message-id`, `in-reply-to` or `references` no longer render beside the
  transport's own. Email replies composed earlier keep their old envelope, which had
  no recipient and could not be delivered. The unread
  `ChannelBackend.message_platform` attribute is removed.

- A triage task's hold may carry a wake date, an awaited user, both or neither:
  `TaskWork.snooze(until=None, *, awaiting=None, expected_revision=None)` takes the
  write and revision check accept and decline take, reads the persisted stage
  under the row lock and refuses a hold awaiting its own holder.
  `TaskWork.wake(reason="manual", *, message=None, expected_revision=None)` ends a
  hold and sends `angee.work.events.task_woken` (`task`, `reason` of `due`, `reply`
  or `manual`, `message`); the task carries no instance sudo and keeps the waking
  actor, and a consumer asks its review again there. New columns `snoozed_at` (set
  exactly while held; the triage inbox filters by it) and `snoozed_awaiting` (the
  awaited user); `snoozed_until` is no longer editable. One rule decides a reply,
  `TaskWorkQuerySet.wake_replied(message, replier_id=)`: a hold wakes only on its
  awaited user's message, so the reviewer's own comment and any message to a hold
  awaiting nobody wake nothing; such a hold wakes only at its date or by hand.
  `wake_from_chatter_thread` is replaced by `wake_on_chatter_reply`. The due job
  and the reply paths wake row by row through `TaskWorkQuerySet.wake_each`, with
  history and a revision bump, where they used bulk updates; a failing row is
  logged and stays held for the next run without failing the others or the
  message that woke it. `held()` leaves out trashed tasks. Leaving triage ends a
  hold. GraphQL `snooze_task(task, until, expected_revision)` makes `until`
  optional; `wake_task(task, expected_revision)` is new, with its MCP tool.
  Upgrade: a host authors the schema migration for the two columns and the
  `snoozed_until` option, then a data migration that marks existing holds and
  clears holds left outside triage, through the historical model and the schema
  editor's alias:

  ```python
  def mark_holds(apps, schema_editor):
      tasks = apps.get_model("projects", "Task")._base_manager.using(schema_editor.connection.alias)
      # Historical models keep Meta.ordering on ``sqid``, which migration state does not
      # carry, so a joined update must clear the ordering.
      held = tasks.filter(snoozed_until__isnull=False).order_by()
      held.filter(stage__category="triage").update(snoozed_at=models.F("updated_at"))
      held.exclude(stage__category="triage").update(snoozed_until=None, snoozed_by=None)
  ```

  `snoozed_at = updated_at` only approximates when each hold began. A migrated hold
  whose wake date has already passed wakes on the due job's first run, and each
  wake sends `task_woken`.
- Intake asks a requester to clarify a request: `Task.clarify(question, *, to=None,
  reply_by=None, expected_revision=None)` (GraphQL `clarify_task`) locks the task,
  gives a requester without an account a passwordless one
  (`Need.provide_requester_account`, the linking the `["*"]` policy uses: no
  credential, admission or membership), holds the task awaiting that account,
  posts the question as a task comment, and also emails it as a reply on the
  request's source message when the requester has no admitted account and the
  request came as an email with a sender on a channel with an outbound sender (the
  reviewer needs the channel's `reply`); a requester reachable neither way is
  refused. `to` names the request to ask about. An inbound email answering a
  message on a request's conversation (its `In-Reply-To` resolved there), while
  its task is in triage, from that request's sender or from an address its
  `Reply-To` named (read through `Message.reply_addresses`), is the request's reply: it files
  no new request and task, and wakes the hold when it awaits that requester's
  account. A relayed request's requester is its `Reply-To` address, which their
  account is created for. A fresh email that only reuses a subject, and any email
  after accept or decline, is a new request. `intake_requester_domains = ["*"]`
  gives every claimed webform address a passwordless, unadmitted account on
  capture; the intake README states its trust model. The intake Task donor
  `TaskIntakeFilters` is renamed `TaskIntake`.

- Failed or retried tool calls are now recorded as failed. pydantic-ai reports
  them as a `ToolReturnPart` whose `outcome` is not `success` (`retried` for a
  tool raising `ModelRetry`, as built-in tools do on every error), which the
  in-process runtime used to save as `completed`. The chat view shows them as
  errors; turns saved before keep their `completed` status.
- A `start_conversation` step may require grounding evidence:
  `StartConversationConfig(grounding=GroundingPolicy(search=("web_search",), fetch=("fetch_url",)))`
  names the tools that satisfy each requirement. One completed call to a named
  tool meets a requirement, read from the completed turn's saved calls. A
  non-empty answer that leaves one unmet fails the step with the unmet
  requirements and every call the turn made; an empty answer still routes
  `no_reply`. Operator Retry posts another turn whose prompt ends with the
  reason. A completed call proves only that the named function tool returned
  without raising during the turn, so a grounding tool raises on empty results
  or HTTP errors; provider-native tools are not saved and cannot meet the
  policy. `AgentTurn.tool_calls(*names)` folds a turn's saved ACP tool updates
  into one `acp.schema.ToolCall` per `toolCallId`, ignoring null fields, selected
  by tool name: a saved tool call's `title` is its tool key (the `SessionRunner`
  contract). What agents store is unchanged, and a step without `grounding`
  behaves as before.

- `angee.scheduling` owns due rules: a `DueRule` (`angee.scheduling.due`) names due
  anchors the subject supplies later and labelled terms measured from them; the
  [scheduling README](src/angee/scheduling/README.md) lists what a rule expresses.
  A consumer stores it in a `DueRuleField` beside its own date or instant column;
  the pure `DueRule.resolve` returns a `Due` whose point and canonical JSON basis
  the consumer writes, and `Due.get_status` is computed on read and never stored.
  `projects` tasks and milestones compose it (below).
- `projects` templates: a project with `is_template` is copied by
  `Project.objects.create_from_template(template, idempotency_key=, values=, task=,
  anchors=, role_mapping=)` (idempotent per actor and key; template read and project
  create, and with `task=` write on the task, whose promoted project is built in one
  insert under the task's lock). The copy carries its phases through
  `Milestone.setup_template_values`, its open tasks and subtasks with their audience
  and relations, the tags (through the new `TagAssignmentManager.copy`) and every due
  rule; dates, statements, grants, participants, bindings, links and files stay
  behind. `Task.assignee_role` names a consumer's role, which `role_mapping` maps to
  a person. `Project.created_from_template` records the provenance. A project with
  closed tasks or reached phases cannot become a template. Ordinary lists, boards
  (projects' and work's), My Work, the project picker, attention and overdue phases
  leave templates out; a Templates page lists them. GraphQL:
  `create_project_from_template`, the project resource's `is_template` and the task
  resource's `in_template` filter (a boolean, false for a task about no project).
  Contributors extend `template_copy_values()` on Project, Milestone and Task and
  `validate_template()` on Project.
- `projects` due dates: `Task` and `Milestone` compose `RuleDatedMixin` (`due_rule`,
  `due_basis`), resolved into `Task.due_date` and the new aware `Task.due_at`, and
  into `Milestone.target_date`, against the project's due anchors (`created` and the
  named `Project.due_anchors` that `set_due_anchor` records). A person's edit or a
  source's stated `schedule` holds the date (`due_basis.held`), even at the rule's
  value; `reset_due_to_rule` releases it. Closed tasks and reached milestones keep
  their dates. Rules over counters are refused, and a rule that cannot resolve when
  anchors move records why (`due_basis.unresolved`). When a save moves a task to
  done by any path, a repeating rule creates its next task once
  (`Task.repeated_from`, `Task.repetition_values()`), due from inputs stored on it;
  a failed repetition is logged and the completion stands. GraphQL:
  `set_project_due_anchor`, `reset_task_due_to_rule`, `reset_milestone_due_to_rule`,
  and `due_rule`, `due_basis`, `due_status` on tasks and milestones.
  `MilestoneTemplate` takes a `due_rule`. The task form edits `due_rule` as raw JSON
  until scheduling ships a rule editor.
- `projects.Task.recurrence` is removed: a task's repetition is its due rule's
  `repeat`. Projects author
  [`task_recurrence_due_rule`](src/angee/projects/compose_migrations/projects/task_recurrence_due_rule.py)
  as their next `projects` migration. It adds `due_at`, `due_rule` and `due_basis`
  to the task table and its history table, moves each recurrence into "the date set
  by hand, repeating" (a `COUNT` becomes the `UNTIL` of its last point, the task's
  date counting first), skips and counts blank or malformed rules, and removes the
  column from both; it is irreversible. Then run `makemigrations` for the release's
  other columns: `Project.is_template`, `created_from_template`, `due_anchors`;
  `Milestone.due_rule`, `due_basis`; `Task.assignee_role`, `repeated_from`.
- `angee.scheduling.fields.TimeZoneField` stores an IANA zone name and
  `angee.scheduling.zones.ZoneName` is the same rule for Pydantic inputs; a blank
  zone takes its context's zone and finally the project `TIME_ZONE`, which
  `get_zone_info` returns. `Recurrence.occurrences` takes a keyword `zone` (blank
  keeps the project `TIME_ZONE`), and `Recurrence.get_occurrence_after` returns the
  next occurrence after a moment. `angee-scheduling` now declares `angee-base`,
  which it reached through `angee-graphql` before. Follow-up: the zone strings in
  `arp.hr`, `messaging` and `posts` compose the new owner.

- IAM sends sign-in links: a person without a password signs in once through
  an emailed link. `User.issue_sign_in_link()` is an account verb with its own
  `auth/user#issue_sign_in_link` permission (admin by default), the usual
  protected-account refusal, a valid-email check and a per-account cooldown
  (`ANGEE_IAM_SIGN_IN_LINK_COOLDOWN`). The console mutation is
  `issue_user_sign_in_link`. The new internal `iam.SignInLink` row keeps a
  keyed token digest, a digest of the account facts it is bound to (email,
  password, last sign-in), the issuer, `expires_at`, `sent_at` and `used_at`,
  never the token. Only an account's newest sent link signs in, and
  deactivation retires its links. The link goes to `User.email` through
  Django's `MAILERS["default"]` once the row commits. Redemption is
  `authenticate()` through `angee.iam.auth.SignInLinkBackend` with
  `sign_in_credentials(token)`, behind the public `redeem_sign_in_link`
  mutation and IAM's public `/sign-in-link` route. A request now makes one
  sign-in attempt: `login` and `redeem_sign_in_link` share
  `angee.iam.auth.authenticate_once`, and a second attempt in the same request
  (aliased or batched fields) is refused. IAM now owns the django-axes policy
  (axes 9 or later): `AXES_COOLOFF_TIME` (one hour; lockouts were permanent;
  a successful sign-in clears no failures), and username and whitelist
  callables, so link attempts are recorded under the account public id they
  name and a genuine spent link is not a failure. `ANGEE_PUBLIC_ORIGIN` is now
  a base setting, fed by the environment variable of that name or authored,
  with one rule (`angee.base.origins.public_origin`): only a bare `https`
  origin (`http` only under `DEBUG`) joins the CSRF trusted origins, so an
  origin authored in `settings.yaml` is now trusted too, and `angee.E051`
  reports an invalid one (an `http` origin without `DEBUG` now fails the
  check). Browser error reports drop URL fragments, frame locations included.
  `safeRedirectPath` resolves a `next` value as the browser does and accepts
  only a same-origin result, closing `/%09/host`-style escapes.
  `useSignInRedirect` (`@angee/app/auth`) is the post-sign-in navigation
  `LoginPage` uses. Upgrade: compose and author the
  `iam` migration for the new table, then re-sync the permission schema (iam
  revision 25). Projects whose `AUTHENTICATION_BACKENDS` replace IAM's list add
  `angee.iam.auth.SignInLinkBackend`; projects with their own
  `AXES_WHITELIST_CALLABLE` or `AXES_USERNAME_CALLABLE` keep theirs and lose
  the link exemptions.

- Tags gain categories. `tags.TagCategory` (name, description, a fractional
  `position`, an optional `max_per_record`) groups the vocabulary for every
  taggable type, and `Tag.category` files a tag under at most one of them
  (nullable; deleting a category un-files its tags). Tag administrators curate
  categories as they curate tags (`tags/tag_category`; tags revision 8).
  - **One check:** `TagAssignmentManager.require_category_maximum` refuses a
    record over a category's maximum, counting archived and unreadable tags.
  - **Edge inserts:** every insert is judged in `TagAssignment.save`, for its
    record in its tag's category. That covers the `tag` verb, a record's `tags`
    input, a changeset revert's restore, a resource load and `objects.create`,
    under the record's and the tag's row locks.
  - **Tag saves:** a tag save that changes its category judges the records holding
    the tag.
  - **Category saves:** a category save that lowers its maximum judges its records
    ("records already hold more").
  - **Merges:** a merge is judged after its moves, through `pre_merge`. Merging a
    duplicate into a capped tag collapses it, and a merge that would leave a
    record over a maximum rolls back.
  - **Category-side locks:** a category-side write locks the category row and its
    tags' rows, so it and concurrent edge writes judge each other's committed
    state.
  - **Bypasses:** edge `bulk_create`, and queryset `update`/`bulk_update` of
    `category` or `max_per_record`, are not judged.
  - **Console:** a categories page, and a `category` column, facet and form field
    on tags. `angee.tags.recordTags` shows a labelled group with one picker per
    category, each showing its maximum.
  - **Upgrade:** hosts author a migration for `tags` (`TagCategory`,
    `Tag.category`) and re-sync the permission schema.

- Decisions answer for a represented record, replace questions atomically and
  remind once. `Decision.decide(..., on_behalf_of=, evidence=, note=)` takes
  `EvidenceReference`s (model, public id, pointer) and stores `answered_for`
  and `answer_evidence` rebuilt from the resolved records, plus `answer_note`;
  `answered_by` stays the actor, `act` is unchanged, every participant must
  read each record, and GraphQL shows each reference only to its record's
  readers. `Decision.objects.supersede(decision, request, *, actor, reason)`
  asks the replacement and withdraws the old question with its
  `withdrawal_reason` and a `superseded_by` link in one transaction, in the
  asking owner's system context (any named system context), and refuses a
  question a requesting record (a workflow step) retains. Escalation is a
  supersede of `decision.replacement_request(due_at=, remind_after=,
  assignees=)`, which derives the request from the stored question and never
  copies its instants. `withdraw` takes an optional `reason`. Requests carry
  the asker's `due_at` and `remind_after`; overdue (`Decision.objects.overdue()`,
  `Decision.is_overdue`, the `is_overdue` field and filter) is false for an
  undated question. `Decision.remind()` stamps `reminded_at` once, returns
  whether it sent `decision_reminder_due`, and runs receivers without
  decisions' elevation; the `decisions.remind_due` beat task runs a bounded,
  skip-locked sweep with a savepoint per reminder and shows a
  `decisions.reminder` timeline line. `decisions/decision` gains
  `read_granted` (assignees, requester, admins) and `read` derives from it:
  consumers union outside audiences into `read_granted`, and fragments that
  still add `read` arms (workflows, `tests/extcontrib`, arp) move there next.
  Upgrade: decisions depends on `angee-jobs`; hosts author the decisions
  migration (`answered_for`, `answer_evidence`, `answer_note`,
  `withdrawal_reason`, `superseded_by`, `due_at`, `remind_after`,
  `reminded_at`, the widened `decisions_verdict_consistent` constraint and the
  `decisions_reminder_due` index), re-sync the permission schema (decisions
  revision 8) and restart the whole application.

- A record-visibility file takes its access from its attached records, its
  direct viewers and admins. Its owner (the uploader, by default) holds it while
  the upload is a draft, which the record edge needs, and once it is published
  manages it only while its records let the owner write it, so whoever loses the
  record loses its record files, the ones they uploaded included. An inherited
  file keeps its owner's access. `storage/file` gains the `inherited_owner` and
  `draft_owner` relations and the `owner_writer` permission, which `transfer` and
  `delete` use. An unattached record file is reachable only by its viewers and
  admins, so narrowing one leaves its owner without it. Operators re-sync the
  permission schema: storage revision 13.
- A record's chatter thread has no owner. `ThreadAttachmentManager.ensure_for_record`
  no longer makes the record's creator its owner, and releases the owner an
  existing chatter thread still carries the next time the record attaches it, as
  it already did for broadcasting hosts. The creator keeps its attribution
  (`created_by`) and reaches the thread, its messages, parts and their files only
  through the record (`messaging/thread#chatter_read`). A record type that
  contributes no `chatter_read` arm (the `ThreadedModelMixin.thread_attachments`
  contract) now lends its chatter's generic reads (part files, follower
  identities, colleague pickers) to nobody but admins, where its creator used to
  reach them as owner. Upgrade: such a record owner contributes the arm, and a
  host releases the owners existing chatter threads carry with a data migration
  over its historical models, for example
  `Thread.objects.filter(attachments__role="chatter").exclude(owner=None).order_by().update(owner=None)`
  (historical models keep `Meta.ordering` on `sqid`, which migration state does
  not carry, so a joined update clears the ordering).
- `ThreadedModelMixin.thread_moderate_access` (default `write`) names the record
  permission chatter moderation requires: editing and deleting other people's
  comments, trashing and restoring comments, listing the removed ones and managing
  another party's follow. `can_moderate` and the record thread's moderation facts
  (`can_trash`, `can_edit`, `can_delete`, `removed_message_count`) read it, so a
  record whose `write` reaches people who may move it but not edit it declares a
  narrower permission. Hosts that declare nothing keep moderation on `write`.
- `StatementsMixin`'s subject verbs are `confirm_statement(*, aspect, values, **members)`
  and `dismiss_statement(*, aspect, **members)` (were `confirm` / `dismiss`), so a
  subject model's own `confirm` verb, such as a lifecycle transition, no longer
  shadows or is shadowed by the statement verb. `Statement.confirm()` /
  `dismiss()` (the stance row's verbs) and every GraphQL mutation keep their
  names; callers of the subject verbs move to the new names.
- `EditableLineField.readOnlyWhen(row, parentRow)` locks one line cell per row
  from the row and its owning document, so a document's section and note rows
  read their commercial cells while their text and row actions stay editable.
- An adopted Person (`Party.objects.for_user(user, adopt=)`) is owned by its
  account, like a Person the account's creation makes, so the account reads and
  edits its own private fields. `follow_user_name` no longer renames a Person
  whose `identity` aspect a source states: survivorship keeps the source's name.
- `UserManager.create_person_as_system(..., identity=)` carries an existing
  identity record on `person_created`, and `Party.objects.for_user(user,
  adopt=)` links that Person to the new account instead of creating another:
  an integration that imported a person's contact provisions their account
  onto it. Adopting a Person another account holds refuses and rolls the
  account back.
- A `workflows.workflowextension` row may declare `points` over its own nodes,
  and a fragment composed after it may splice there with `at`: a dependent
  addon's phase runs at a declared point inside another addon's fragment, ahead
  of the nodes that follow that point. Base points splice first, then fragment
  points in declaration order; a point used before its
  declaring fragment, or declared twice, refuses the load. Published
  contributions retain the declared points and the authored routes a splice
  rewrote, so a narrow reload recomposes the same graph; documents without
  fragment points keep their published shape and fingerprints.
- The `composed_tables` test fixture is removed: a database test requests
  pytest-django's `db` or carries a `django_db` mark, and rolls back over the
  session baseline `angee.base.testing.fixtures` records. The
  `angee.parties.testing.fixtures` plugin and its `statement_sources` fixture are
  removed too; `angee.provenance.testing.fixtures` loads every installed addon's
  statement principals into that baseline.
- `storage/file` derives `read`, `write` and `share` from the new
  `read_granted`, `write_granted` and `share_granted` alone and withholds a
  trashed file from everyone who cannot delete it, as knowledge pages and
  dashboards do. Fragments union their file arms into the granted permissions
  (messaging's `message_part`, projects' `task_record` and the notes example's
  `note_record` moved); the security check `storage.E001` refuses an arm on
  `storage/file` `read`, `write` or `share`. Operators re-sync the permission
  schema: storage revision 12, the messaging fragment 7, the projects fragment
  10 and the notes example fragment 4.
- Systems of record state projects, milestones and tasks. Each composes
  `StatementsMixin` through `angee.projects.models.StatedRecordMixin` and declares
  `StatementRecords` (identity, lifecycle and schedule; a task adds priority,
  assignment and structure), and takes a person's edit as a stance only once a
  source states the row. A source is admitted by the new `write_statements`
  permission alone (`write + statement_source`), never through `read` or `write`,
  so no row inheriting a project's or task's access reaches it. A stated lifecycle
  applies through the row's own lifecycle verbs, and a row carrying statements
  refuses deletion. Parties keep `statement_source` out of `read_as_subject` and
  `write_as_subject`, so a record task about a party lends that party's source
  nothing. Upgrade: a host project runs native `makemigrations` for the new
  `ProjectStatement`, `MilestoneStatement` and `TaskStatement` tables and their
  history tables, then runs `rebac sync` after migrate for projects revision 16
  and parties revision 19. A composer of the abstract `projects.Task` under its own
  resource type declares `permission write_statements` in its schema and keeps a
  concrete `milestone` foreign key: the task's `structure` aspect copies
  `project`, `milestone` and `parent` into its statement table.
- Stages: the `angee.base` stage primitive owns every user-named pipeline.
  `Stage` gains `description`, `fold` (a board's default-collapsed lane, read as
  `foldField`), `rotting_days`, `for_record` applicability, a
  `validate_transition(previous, following, *, manual, admitted, adding)` admission
  hook and idempotent `provision(container, rows, key=...)`. `Stage.resolve_default`
  becomes `resolve_default(container, *, stages=None)` and
  `StagedModelMixin.resolve_default_stage` always passes the record's applicable
  stages, so an override of `resolve_default` accepts the keyword-only `stages`,
  and `resolve_default_fallback` receives those applicable stages rather than
  every stage of the container. `StagedModelMixin` gains `stage_entered_at`,
  `stage_scope_fields`, `stage_container()`, `stage_transition`, `project_stage`,
  `writes_stage`, the `carry_instance_facts(stored)` and
  `validate_stored_row(persisted, stored)` hooks and `save(admitted_stage=...)`. A
  save reads the committed row once under its lock and judges a partial save on
  the row it stores. Every save and `clean()` requires the stage to belong to the
  record's container, read from the stage row admission already loads, so a record
  whose stage moved to another container refuses every write. Applicability is
  judged only when the write changes the stage, the container or a
  `stage_scope_fields` input, so a record resting in a stage later narrowed away
  from it stays editable; a record whose `Stage.for_record` override reads its own
  fields lists them there. Admission asks `validate_transition` only about a move:
  a write that keeps the stage is not admitted, so an override drops any
  same-stage guard. `work` composes it (its private transition read, provisioning
  loop and same-stage guard are gone; `Stage.validate_transition` takes the new
  signature), and a work stage's `queue` is write-once (`ImmutableFieldsMixin`),
  which replaces the narrower check that kept only a phase-mapped stage in its
  source task's queue.
  Projects run `makemigrations` for the new columns. Existing staged rows keep a
  null `stage_entered_at` until their next move; a project that wants dwell time
  at once authors an optional data migration setting `stage_entered_at` to
  `updated_at` where `stage` is set (an approximation: the last write, not the
  last move).
- Native lists accept `relatedModels: readonly string[]` for columns and filter
  membership derived from related rows. Declared model changes refresh flat,
  client and grouped lists when `related_records` names any record of the list's
  model, including records outside the page. They share authored reads' matcher
  and the existing non-cancelling 300 ms / 2 s live coalescer.
- Timeline invalidations use `timelineChanged(model: String!, id: ID!): Boolean!`
  on the console schema, with the timeline read's record authorization at
  admission and each delivery. Synthetic record-feed updates are removed.
  The rail and attention read use Refine's shared 300 ms / 2 s coalescer over
  their own query keys, so workflow/provenance activity no longer refreshes
  unrelated inbox reads of the record's model.
  The existing Refine patch makes native subscription effects track their
  channel, arguments, provider and callback across navigation, removing the
  actor-keyed user-preferences remount workaround.
- Download tokens use Django timestamp signatures aligned to 300-second windows.
  URLs are stable within each window; the 900-second maximum age leaves
  600–900 seconds of validity from issue. Download lookup still checks expiry
  and the issuing actor's current read permission. URLs include the content
  digest for cache versioning. Preview renderers and storage gallery images
  retain their loaded URL by public file ID plus digest across token rollover;
  changed content or a different file loads the current URL.
- The provenance typing plugin preserves its declared protocol when Django
  has no configured storage setting; it does not invent that setting. Bare-host
  fixtures declare the required settings of the capabilities they exercise.
- The provenance typing plugin types an abstract composed source's history and
  statement rows as those of the model registered under its label (a `Project`
  source's `history` yields `HistoricalProject`); only a pure mixin keeps its
  declaration's row bound. `StatementPair.statements` is typed as its descriptor,
  so a pair row reads its statements as the class does. Framework `merge_policies`
  redeclarations take the owner's `ClassVar[Mapping[str, Any]]`, so a consumer
  donor composing another owner-typed base passes mypy's base-compatibility check.
- Source retirement: `ChangesetQuerySet.history_written_only_by(record, predicate)`
  shares document history selection with `touching_filter`, requires a creation
  snapshot and refuses unmatched or unattributed writes. The reference owner adds
  `owned_reference_parts` and `has_live_references`; Statement declares retained
  authorship/current-state policy. `PersistenceMixin.owned_relations` declares
  document parts once for history and live-reference retirement. Stored relationship
  exclusions compose `named_in_relationships(exclude=...)`.
  Creation undo uses the same live-reference owner without an authorship exclusion.
- Stream apply contract: `ApplyResult.mapping_version` is removed. The driver
  stamps the stream declaration's `mapping_version` at promotion and quarantine;
  `RecordChange` and `LocalChange` carry no version copies. A lower declaration
  updates the active stream mapping without resetting progress; a later increase
  replays rollback output. Mapping changes fence already-extracted pages. Notion
  and the framework's adapter fixtures use this contract.
- Integration branding: the existing public vendor projection carries slug and
  display name together. Unnamed integration and inbox channel labels fall back
  to that branding; stable labels and connection selection compose the same
  owner. GraphQL annotates the branding instead of prefetching a protected Vendor.
  Fallback branding is cached per instance, including reads by `__str__`;
  the Vendor catalogue remains admin-only. Record-reference guards explicitly
  allow one bounded represented-content-type read per compile, included in
  statement budgets; declaration and optimizer probes remain query-free.
- Reader scopes: `base.permissions.reader_queryset(source, actor)` pins a given
  actor and otherwise follows ambient scope. It accepts a model or preserves an
  existing queryset's subtype and filters. Relation reads, record-target
  prefetches, sender-name projections, party/circle reads, history, statement
  endpoints and evidence display compose it; same-model source querysets used
  only for reader selection are removed. Evidence admission still requires an
  explicit actor.
- Query checks: `angee.base.testing.queries` classifies captured SQL by its outer
  statement, including CTE-prefixed reads and writes; `angee-base[testing]`
  declares `sqlparse>=0.6,<0.7`. Its shared `capture_planner_memory` measures
  parameterized statements with PostgreSQL 17's `EXPLAIN (MEMORY)`. Timeline,
  inbox parts, and composed member person/projects reads now gate planner
  allocation; document gates sum the separately planned SELECT allocations.
  SQLite statement/text ceilings remain. Inbox SQL also bounds every message-table
  permission CTE body: one on PostgreSQL, five distinct permission stages on SQLite.
- OIDC: JWKS fetches compose `integrate.oauth.client.oauth_get_json`, sharing
  discovery/userinfo headers and private-provider policy. Transport, status and
  JSON failures map to `PyJWKClientConnectionError`; PyJWT retains key parsing
  and JWK-set caching.
  Direct disconnect-guard tests now hold the caller-owned transaction through
  admission, matching production, and run in PostgreSQL CI so user locks are exercised.
- Statements: `StatementsMixin.statements` declares the subject-bound statement
  descriptor contract for donors; `StatementRecords` installs the concrete accessor.
- `StateTransitionsMixin.transition_fields` replaces `_transition_fields` as the
  public set of companion field names, defaulting to `frozenset()`.
  `save_state` writes them with the guarded state and resets the collection even
  when its concurrency check or save fails. Callers assign/read the attribute
  directly and reset it with `frozenset()`, never `hasattr`/`delattr`.
- IAM authentication checks Django's configured `get_user_model()` so composed
  and swapped user models share the active-person login contract.
- Jobs consumes provenance's optional `ANGEE_CELERY_TASK_CLASS` setting; without
  provenance's scalar default it uses Celery's native `DjangoTask`.
- Declaration owners: `decisions.mixins.DecisionTargetMixin.decision_methods`
  defaults to `()`; declarers inherit it. Party identity review contributes its
  opt-in through `workflows_parties`, keeping parties independent of decisions.
  `HistoryDocumentMixin.timeline_subject` defaults to `True` and controls
  provenance timeline admission.
  `ChangesetHistory.provenance_history` defaults to `None` until the native
  history factory binds its capture declaration; `ModelHistory` composes it
  alongside custom history bases. `ImplBase.key` retains its `""` default;
  `StorageBackend.key` explicitly defaults to `""` for registry-only native
  backends, while nonempty keys must match their registry entry.
- `StatementField` is an explicit Django field base with a `for_statement`
  default returning the copied field. Custom adapters inherit it; ordinary
  fields are unchanged. Document-save backends extend `AngeeHasuraWriteBackend`.
- `base.addons.addon_contribution` reads ordered iterables, mappings and callable
  exports with typed return variants. Mapping factories can require a callable;
  callable exports are returned without invocation. ASGI protocols, GraphQL
  schemas and MCP registrars share this owner; malformed present exports still
  fail, and absent exports contribute nothing.
- Autoconfig owns optional settings defaults. Bare settings modules using these
  capabilities must explicitly declare `ANGEE_BUILTIN_MCP_URL`,
  `ANGEE_RESOURCE_EXCLUDED_ENTRIES`, `ANGEE_CHANGESET_MODEL`,
  `ANGEE_ADDON_INSTALLER_BACKEND`, `ANGEE_OPENCODE_OAUTH_ENABLED`, and the
  operator's `ANGEE_OPERATOR_GRAPHQL_ENDPOINT`, `ANGEE_OPERATOR_URL`,
  `ANGEE_OPERATOR_TOKEN` and `ANGEE_OPERATOR_RESTART_JOB`; readers no longer
  invent missing-setting defaults.
  Money declares `ANGEE_MONEY_REFERENCE_CURRENCY=None`; conversion still requires
  a project-selected currency. Named hooks are optional: an absent setting means
  inactive because its declaring addon may not be installed, and the shared hook
  resolver then contributes no callable. Live implementation registries and
  transition policies remain required inputs; empty registries and `None` policy
  overlays express inactivity. The optional provenance typing plugin leaves its
  declared types unchanged when no storage binding is configured. Retained
  migration fields may outlive retired settings.
- Base: `AngeeModel` declares `change_read_resource`, `change_related_records`
  and `change_batch_targets`; adapters dispatch through these contracts.
  `AngeeQuerySetMixin` and `SCHEMA_BINDING` replace their underscored names.
  `relation_read_queryset` now lives only in `base.permissions`; ordinary
  relation reads use a pinned actor or REBAC's ambient scope. Knowledge page-tree
  expressions compose that owner and use fresh related selections, independent
  of the outer page filter. Actorless runtime reads fail closed; explicit system
  reads are unscoped. `prefetched_objects` reads Django's materialized ordinary
  relation cache without evaluation (`None` is a miss, `[]` an empty hit).
  `base.permissions.user_subject_ref` is the canonical inverse of `actor_user_id`;
  bare user keys no longer trigger a database read. Thread reader grants and
  revocations share one existing-user lookup; the unused messaging alias is removed.
  Proposal responders and storage's wildcard share compose the canonical owner.
  `retained_reference_exists(cls, lookup)` drops the unused
  `record` argument; overrides and callers must use the lookup-only signature.
- Base writes: immutable changed values retain their identity; mutable values
  are copied before observers run. `bulk_updated` carries only `instances` and
  `update_fields`, without `using`. The public `send_bulk_update` delivers that
  batch robustly and logs every failed receiver; trash, provenance and workflows
  share it. `PostgresAdvisoryLockBackend` and its handles no longer carry an
  `alias`; they use Django's default connection.
- One database: owner-initiated database selection has been removed across base,
  provenance, timeline, messaging, posts, parties, projects, work, knowledge,
  proposals, resources, IAM, storage, sequence, decisions and workflow verbs.
  `using` is removed from `initialize_rows`, `canonical_ids`, `record_view_refs`,
  `FractionalRankField.allocate_missing_ranks`, `Task.ordering_write`,
  `changeset`, `_ChangesetBoundary`, `ChangesetContext`, `Aspect.coerce_members`,
  `history_related_scopes`, `ModelHistory.changeset_states`, `build_resource`,
  `resolve_xref`, `resolve_ledger_xref`, `resolve_content_type` and
  `materialize_grant_groups`. Resource widgets no longer carry a `using`
  attribute, resource construction no longer sets `using_db`, and `pre_load`
  sends no alias. Native Django/history hooks retain their handed aliases;
  import-export's own lifecycle retains `get_db_connection_name`, with no
  Angee-initiated calls. `angee.E050` requires exactly `DATABASES['default']`
  and empty `DATABASE_ROUTERS`.
- Provenance: `_ChangesetScope` / `ChangesetScope` becomes `ChangesetContext`,
  distinct from the existing `Changeset.scope` and `ChangesetScopeMiddleware`.
  `_ChangesetHistory` and `_UNLOADED` become public `ChangesetHistory` and
  `UNLOADED`. Command and resource-load attribution no longer select a database;
  bulk history capture no longer partitions rows by database. Member timeline
  reads have statement and SQL-size budgets.
  History, field provenance, statement links and evidence display now share
  ambient read policy: system scope without an actor can read, while strict
  actorless access fails closed. Evidence admission still requires a named reader.
- Saved rows: `base.validators.SavedRow` is the shared Pydantic annotation used
  by `DecisionRequest.records`; `is_saved_row` exposes the same predicate to
  other callers. A row requires a primary key and `_state.adding == False`.
  Constructing `Model(pk=...)` does not make it saved; validation names the
  containing field and preserves nested error locations.
- State and hierarchy: `StateTransitions` accepts abstract donor declarations
  against abstract source fields and binds once to the composed host, refusing
  collisions. `TaskWork` uses that guard and explicit `save(admitted_stage=...)`
  for owner-authorized stage entry; its private status flags and `__setattr__`
  workaround are removed. Projects-only hosts remain unguarded by work, and
  work hosts still reject constructor-authored status. Public
  `validate_hierarchy_parent` and transactional `validate_hierarchy_move`
  replace private ancestry access; refused moves restore the in-memory path.
- Messaging and posts: `angee.posts.ingest` is removed; use `Message.posts.land`
  and `land_relations`. Parsed envelope hashing lives on `ParsedMessage.sync_hash`
  and `ParsedPart.sync_digest`, replacing the manager's private hash helpers.
  `ChannelQuerySet.outbound_email(backend_class=...)` is a lazy, chainable
  predicate. `require_outbound_email` on that queryset and its manager raises
  `OUTBOUND_EMAIL_NOT_CONFIGURED` or `OUTBOUND_EMAIL_CHANNEL_MISSING`; callers
  needing an admission refusal must use the verb. `Channel.outbound_sender`
  retains those coded, channel-keyed refusals; invalid sender setup also names
  the channel field.
  Comment timeline reads gate the host's thread permission in SQL and return
  no comments when a retired record redirects to unreadable chatter.
  Threaded-record and audience contracts live in `messaging.mixins`; historical
  public imports from `messaging.models` remain aliases to those same classes.
  The moderation audit reason is `posts.message.moderation`.
- Proposals: manager access composes the task owner's `read_granted` and
  `share_granted` rules instead of overriding derived permissions. Managers
  cannot read withheld task content or record-bound tasks they cannot otherwise
  read. The proposals permission extension revision is 11; deploy with
  `rebac sync`.
- Resources, IAM and storage: `_NativeJSONWidget` becomes public
  `NativeJSONWidget`. Post-load hooks dispatch through `ResourceLoadMixin`, which
  hook authors must compose. `AngeeResource.adopt_persisted_instance` copies a
  verb-persisted row, marks it saved and completes the import lifecycle for
  chatter and workflow-definition adapters. Dashboard loads revalidate parents
  in one read. Mount connect helpers and `MountManager.provision` now require
  `MountMode` values; the manager no longer coerces strings.
  `resources.E001` rejects models defining `after_resource_load` without
  composing `ResourceLoadMixin`.
  `File.purge_if_unretained` treats
  every reverse foreign key as retention regardless of `on_delete`, including
  a committed `messaging.Part.file` with `SET_NULL`.
- Workflows and extraction: `workflows.transactions` owns `RETRYABLE_SQLSTATES`,
  `sqlstate`, `database_timeout` and `isolate_failure`; private manager copies
  are removed. `StepContext` requires its `runner`. `Settlement` replaces the
  result union and private base with an abstract class; extending it is
  framework-internal, while addons return `Done`, `Wait`, `NextPage`, `Ask` or
  `Fail`. Attempt errors retain exception types and causes and, like stacktraces,
  require run-operator access. Run-level errors retain their writer gate.
  Workflows schema revision 25 requires `rebac sync`. Extraction acquisition
  exposes `text_part` instead of `_text_part`.
- Integrate locks: `bridge_lock_key` and the module-level lock forwarders are
  removed with `integrate/locks.py`. Use `Integration.sync_lock_key`,
  `sync_lock`, `sync_is_locked`, `live_session_host_lock_key` and
  `session_is_hosted`. `Bridge.admit_due_sync` decides from the scheduler's
  already-locked row, with one row lock. A fresh or declined admission claims
  the poll interval; stale queued recovery retains its claim. Declined manual
  admission still writes nothing.
- Integrate mapping upgrades: `StreamDefinition.mapping_version` declares the
  mapping revision; a real increase opens one baseline generation, preserving
  links and resetting the cursor. `SyncStream.mapping_version` is nullable with
  no default: an unknown version is initialized under the integration lock from
  the minimum applied `RecordLink.mapping_version` (`applied_at IS NOT NULL`), or
  the declared version when there are no applied links. Never-applied observations,
  quarantines and pinned links do not lower the seed. Deploying streams whose
  applied links are already current preserves progress and closed snapshots; an
  older applied link triggers exactly one replay.
  Projects adding this column must generate a nullable `AddField` with no default
  so existing streams start unknown. Hosts retaining the earlier column addition
  need `AlterField` to `PositiveIntegerField(null=True, blank=True)`, removing
  `default=1`. That alteration preserves existing numeric values; it does not
  undo an earlier migration's version-1 seed. No per-host link backfill is needed
  for unknown versions. Already recorded versions are preserved, including on
  rollback. Adapters stamp record evidence with their
  declared version, independently of the stream's recorded maximum.
- Integrate progress and OAuth: `BridgeProgressReporter`, `current_bridge_progress`
  and `LiveSession(reporter=)` / `.reporter` are removed. Nested sync code uses
  `current_progress_bridge().report_progress`; `bridge_progress_context` becomes
  `progress_bridge_context`. `ExternalAccount.account_label` owns its display
  fallback; `provider_*` properties now raise for a missing required OAuth client
  instead of returning empty strings. The typed OAuth protocol calls
  `OAuthClient.discover_endpoints` directly; the duplicate OIDC `ensure_endpoints`
  override and parameter-coercion helpers are removed (the base protocol method
  remains). `HTTP_TIMEOUT_SECONDS` is owned by `integrate.http`.
  `HttpClient.get_json` owns JSON GET. OAuth's `oauth_get_json` composes its
  outbound headers and private-provider policy for discovery and userinfo;
  the obsolete private helper and `USERINFO_FAILED` code are removed. Discovery
  translates failures to its coded errors; userinfo remains best-effort.
- GraphQL: `line_metadata` replaces `_line_metadata`; generic adapters call the
  base model's change, deletion and redaction contracts. `AngeeManager.admit_view_as`
  declares a refusing default; IAM's manager owns admission. Record-reference
  filter targets come from `accepted_target_models` without a content-type
  lookup during expression construction. Relation resolvers use base's ambient
  read and ordinary-prefetch owners; actorless fallback does not reuse an
  evaluated actor-bound cache.
  SQL relation guards resolve scopes lazily at compilation, keeping declaration
  and optimizer probes query-free. `angee.graphql.testing.capture_broadcasts`
  captures committed change-feed payloads through the public test seam; its
  context must include the delivering commit.
- Tags: readable extension fields no longer imply filter or group axes.
  Consumers must declare `hasura_filterable_fields` and `hasura_groupable_fields`
  explicitly for each extension field they expose on those query axes.
- Web: imperative TanStack reads use `query` / `infiniteQuery` (react-query
  >= 5.102); `readSharedIdentity` owns the shared static identity read. ACP chat
  removes runtime dispatch props/context and the retired `@angee/agents/chat`,
  `AgentChatProvider` and `useAgentChatContext` exports. `AgentChatTarget.runtime_class`
  is removed from GraphQL; the agent model's runtime implementation remains.
  `AgentChat` incorporates its former `AcpAgentChat` wrapper and drops the
  non-lazy boundary. The detail Chat tab distinguishes loading, errors and
  unavailable chat; roster selection falls back when the picked agent stops.
  Provisioning reads workspace status directly and uses `ErrorBanner`'s null
  state. `ActionFormDialog` enforces required static/dynamic definition fields,
  binds missing-field errors and disables incomplete submission by composing
  `MutationDialog`'s `requiredDialogErrors`. The shared test harness supplies
  zero tooltip delay; hover assertions are synchronous after `mouseEnter`.
- Native embeddings: `InferenceModel.embed` requires an approved model for the
  requested use,
  composes native OpenAI/Ollama embedding bindings and validates batch shape and
  consistent dimensions, recording the size through `embedding_dimensions`.
  SDK client lifetime is shared with chat; discovery records embedding
  capabilities. `knowledge_embedding` and `knowledge_graph_extraction` are
  deployment approval roles for embedding and chat models, respectively.
- Pgvector retrieval: `angee.knowledge_graph_pgvector` implements permission-scoped
  PostgreSQL passage retrieval selected by `Vault.retrieval_class = "pgvector"`.
  Vaults configure `embedding_model`, optional `extraction_model` and
  `graph_entity_kinds`; composed host migrations add these fields and the
  `Passage`, `PassageEmbedding`, `Entity`, `EntityMention` and `EntityEdge` models
  with their constraints/indexes. Provisioning prepares `vector` and `pg_trgm`;
  activating/searching the backend on SQLite is refused.
- Retrieval indexing preserves unchanged passage identity, heading paths and
  exclusive source offsets. Commit-triggered embedding jobs and bounded
  reconcilers fence concurrent work, keep provider calls outside transactions
  and recheck source/model fingerprints before persisting. Search combines
  exact cosine similarity, PostgreSQL full-text ranking and one-hop page links
  using reciprocal rank fusion. Every arm scopes readable live evidence before
  ranking; vectors must be finite and nonzero, and model failures are explicit.
  `search_pages` retains page results;
  GraphQL/MCP `search_passages` adds passage evidence, and `related_pages` uses
  page centroids and links without embedding a query.
- Optional local graph RAG extracts typed entities and relationships from
  passage evidence through the approved inference owner. Evidence and both
  endpoints are permission-filtered before bounded traversal and personalized
  PageRank; graph evidence contributes another fusion arm. Disabling extraction
  immediately removes that arm; stale source/configuration evidence is excluded.
  Global summaries and thematic graph search are outside this capability.
- `knowledge_retrieval_eval` accepts YAML/JSON golden cases, an actor and vaults;
  it reports recall@10 and MRR overall and by factoid/multi-hop category across
  vector, lexical, hybrid, hybrid+G0 and hybrid+G1 rankings. It shares production
  ranking, resolves titles under the actor and does not backfill or mutate the
  index. The bundled synthetic corpus is a starter, not a measured quality claim.
- Dependencies: base requires Pydantic >= 2.14 and imports its public `MISSING`;
  agents requires pydantic-ai-slim >= 2.60 and the OpenAI addon >= 2.66 for native
  compatible-provider embeddings. The optional pgvector addon adds agents, jobs
  and MCP dependencies plus `pgvector>=0.5.1,<0.6`,
  `semantic-text-splitter>=0.33,<0.34`, `networkx>=3.7,<4`, `scipy>=1.16,<2` and
  `pyyaml>=6,<7`. Refresh project locks/environments and regenerate composed
  schemas and clients when upgrading these contracts.
- Tests and CI: installed-model registry restoration and its ordered monkeypatch
  fixture live in `base.testing.fixtures`, shared by framework and resource tests.
  Test module/timing names describe their subjects; obsolete timing entries are
  removed. Both PostgreSQL services in `reusable-checks.yml` use
  `pgvector/pgvector:pg17`, installing `vector` in host and template databases
  before composition/tests. Readiness tests cover native and composed databases.

## Unreleased — domain and capability owners on their rows, managers and querysets

- Storage: `FileManager.ingest_bytes`/`ingest_stream` (and `PartCarrier.retain`)
  return `(row, created)`, as `get_or_create` does. Metadata merge, placement,
  dedup reach and `purge_if_unretained` live on `File`; prune, trash and purge
  selections on the folder and file querysets. Extraction retention locks its
  lineage, then its source and carrier files in one key-ordered
  `FOR NO KEY UPDATE` read, then message parts. Mounts provision through
  `MountManager.provision`; iPhone backups close their backend after a sync.
- Integrate: `Bridge.request_sync(*, now, persist) -> bool` sends an admitted
  sync and `Integration.request_sync` fans out; `angee.integrate.queue` and its
  `queue_bridge_sync` are removed. `Source.complete_sync`,
  `Integration.preview_push`, `Integration.load_connection_credential`,
  `RecordLink.resolve_conflict` and the stream/absence querysets own their
  rules. Credential refresh runs behind a blocking fence and refuses to run
  inside a transaction (`ensure_fresh` raises `RuntimeError` there), and a forced
  refresh of an inactive credential is refused (`ValueError`; the admin
  mutation answers "reconnect it"). Authlib's `prepare_grant_uri` builds the
  authorization URL without a client session or secret and omits an empty
  `scope`. `OAuthClient.discover_endpoints(*, persist=False)` saves what it
  discovers when asked.
- Messaging: `angee.messaging.connect` is removed. Pairing verbs live on
  `Channel` (`pairing`, `resume_pairing`, `submit_pairing_password`,
  `skip_pairing_password`, `reset_pairing`, `disconnect_pairing`) and
  `ChannelManager.resume_or_create_pairing`; `PairingActionError` moves to
  `messaging/backends.py`. `Message.reserve_delivery`, delivery limits in
  `messaging/constants.py`, `Channel.outbound_sender` and
  `ChannelQuerySet.outbound_email` (IMAP admitted when configured),
  `ChannelWebform.submit_webform`, `MessageSubtypeManager.resolve` and
  `PartManager.attach_file`. IMAP credential refusals answer `VALIDATION`
  instead of `BAD_USER_INPUT`. The pairing GraphQL names are unchanged
  (`disconnect_channel` → `Channel.disconnect_pairing`, `reset_channel_pairing` →
  `Channel.reset_pairing`, and so on), and the owner lock is
  `FOR NO KEY UPDATE`. Projects declares the `activity_done` subtype as a master
  resource row: stacks run `resources load`.
- Posts: `MessagePublic` owns the public payload, provider moderation and
  `PROVIDER_TRASH_REASON`; `PostMetricsManager.reply_counts`.
- Agents: `angee.agents.deployments` and `agents/skills.py` are removed
  (`InferenceDeploymentIdentity` lives in `agents.models`; `Skill.parse_manifest`
  replaces `parse_skill_meta`). The built-in MCP server and catalogue are
  `MCPServerManager.builtin` and `MCPToolManager.sync_builtin`. Provisioning
  verbs take a saved `Agent`, return their message and raise
  `ProvisioningError`. Hand-written built-in MCP tools declare
  `meta=ACTOR_EXECUTION_META`; a tool without it fails closed.
  `InstallResult.reconciliation_pending` reports a failed catalogue refresh.
  A nested `OperatorTransportProvider` passes through. The actor-tool check is
  `angee.mcp.graphql.require_actor_tool`; `RESOURCE_READER_TOOL_TAG` lives in
  `angee.mcp.tool_tags`.
- Domains: `Decision.decide`/`withdraw` are row verbs. `Currency.convert` refuses
  non-finite or non-positive operands. Portfolio health is one `Health` enum and
  one GraphQL name; `Update` saves under its target lock and repairs health on
  delete; `Update` rows do not merge (`MergePolicy.BLOCK`). Topic keys normalize
  once (`proposals.validators.normalize_topic_key`).
  Donor querysets are separately named managers (`Thread.spaces`, `Task.work`).
  Triage tasks are audited as `work.task.capture_triage` (was
  `intake.need.create_triage_task`); chatter wakes as `work.chatter.wake`.
- Workflows: the run row owns its state. `WorkflowRun.advance` settles, plans
  and writes status; `Runner` keeps the tick, dispatch and failure
  classification and hands classified failures to `WorkflowRun.fail_advance`
  (`AttemptRecord`, `isolate_failure`). `WorkflowRun.admit_subject`, `interrupt`
  and `cancel_owned_work` are the held-run verbs managers compose.
  `StepRun.wake`/`redispatch`/`reap` report whether to replan. `reopen()` no
  longer replans; callers advance through the runner.
- Parties: `Handle.resolve`, `Party.apply_identity`, `PartyManager.ingest_connections`
  (`parties.connections` keeps only its parsed value types),
  `CircleQuerySet.memberships`, `PartyQuerySet.circle_members_prefetch`,
  `Party.circle_names()` and the GraphQL
  `merge_scalar_fields` on PartyType and PersonType, which the merge page reads.
- Tests: PostgreSQL lock races share `tests/lock_waits.py`.
- Consumers: arp, bridges, odoo and phrogg companions move to these owners.

## Unreleased — one database, base-owned observation and locks, model-owned lines and service users

- One database is the contract. `transaction.atomic()` and `on_commit()` are
  bare in owner verbs; an alias that Django or a library passes into an
  override or hook (`save(using=)`, signal `using`) passes through unchanged.
  System check `angee.E050` requires exactly the `default` database alias and
  empty `DATABASE_ROUTERS`; the static placement test is retired. The owner and
  helper sweeps are complete under the native-hook pass-through rule above.
- Locks: `angee.jobs.locks` moves to `angee.base.locks` with unchanged names
  (`task_lock`, `LockKey`, `record_lock_key`, `task_lock_is_held`,
  `task_locks_are_cross_process`). `task_lock(key, blocking=True)` waits and
  then adopts; on PostgreSQL it refuses a key the same session already holds,
  as the local backend does.
- Observation: write observation moves from `angee.graphql.publishing` to
  `angee.base.signals` (`ModelChange`, `change_observed`, `change_published`,
  `publish_change`, `mute_changes`, `connect_observers`). GraphQL keeps only
  the transport adapter, which fires for models with a subscription feed.
  `change_observed` receivers fail the write; `change_published` observers stay
  robust. `ChangePayload` drops `instance`; the unread ingestion mark
  (`during_ingestion`, `publication_ingestion_context`,
  `angee.integrate.sync.bridge_sync_context`) is removed.
- `change_related_records()` returns `angee.base.refs.RecordRef` values; the
  hook owns any `record_view_refs` widening. `ChangeRelatedRecord.for_records`
  is removed.
- Lines: `AngeeModel.replace_lines(field, rows, *, position_field, replace,
  permission="write")` owns line writes with native validation, temporary
  fractional ranks, row locks and the parent's model-owned `lock_subject()`.
  It always checks the parent permission; a GraphQL nested insert names
  `permission="create"`. `AngeeModel.line_relation` refuses reverse relations
  and other non-columns, at build time for `HasuraLines.writable`. Line writes
  are audited as `base.replace_lines` (was `graphql.hasura.save.lines`).
  `InputExtensionsMixin.pop_input_extensions` is the one input split.
  `FractionalRankField.get_temporary_ranks` takes no `using`.
- IAM: Agent and Workflow compose `ServiceUserMixin`; `UserManager` owns
  `ensure_service_user`, `create_user_from_seed` and `get_for_session`, and
  `UserQuerySet` owns `directory`, `colleagues` and `view_as_queryset`.
  System check `angee.iam.E002` refuses a participant without its `user`
  one-to-one. `angee.iam.service_users` and iam's `ROLE_SUFFIX`/`is_role_type`
  are removed (use `rebac.roles`). The OIDC link and OAuth connect reloads are
  audited as `iam.session`.
- Integrate declares `ANGEE_OIDC_DISCOVERY_TTL` (key unchanged). `runserver`
  closes its preflight connections before serving. FormSpec field annotations
  are one `$defs` entry.
- Consumers: arp, bridges and odoo re-import `angee.base.locks`,
  `angee.base.signals.publish_change` and `RecordRef` hooks.

## Unreleased — run interrupts, identity sharing, annotation and delivery expiry

- Workflows: `interrupted` joins `done`, `error` and `canceled` as a native run
  fallback outcome. A parent must route every frozen child outcome, so a parent
  that awaits a child fails `Definition.check` until it routes `interrupted`;
  frozen published versions keep their contract until republished.
- Parties: Organization now declares `read_private` and gates `notes`,
  `first_met_note` and `handle_count` behind it, as Party and Person do.
  Statement sources and identity readers read the Organization without them.
- Parties: `identity_reader` on the canonical Party shares its basic identity;
  the Person or Organization row reads the grant through its `party` link. It
  lends no private fields, handles or related subject records. Grant it on the
  Party: the MTI children declare no share surface. Handle `read` now derives
  from `read_granted`; the sender Handle stays directory data, and a message's
  reader takes its sender from `Message.source_header_values`.
- Tags on messaging threads and messages are governed by `annotate`, not
  `write`. The channel's grantable `annotator` relation allows tag changes on
  readable conversations without content-edit access; withholding and trash
  still apply.
- Ollama: SDK retries are disabled (`max_retries=0`). Callers own retries within
  their execution budget.
- Workflows: execution messages carry Celery expiry anchored to the database
  `dispatched_at`: the whole redelivery allowance
  (`ANGEE_WORKFLOW_MAX_DISPATCHES` recovery intervals) plus one interval.
  Replacement admission stays at 60 s. Workers are assumed to run at most 45 s
  ahead of the database clock.
- Agents: TypeSafe's SDK is the framework's decision-inference owner.
  `angee-agents` depends on `typesafe-sdk`, and the public `decide` contract of
  `InferenceBackend`, `InferenceProvider` and `InferenceModel` takes its
  `Content` and `Question` types and returns its `SystemOneResponse`. The Ollama
  backend answers through System One.
- Integrate: `Bridge` replaces `next_subscription_refresh_at` with `live_retry_at`
  and `live_retry_attempts`: a transient live-session failure defers the
  reconciler's restart by a doubling backoff instead of latching the bridge for
  repair. Projects author the host migrations for every concrete bridge
  (messaging channels, party directories, VCS bridges, Notion connections and
  the personal-messaging bridges).
- Agents: `angee-agents` now requires `pydantic-ai-slim>=2.60`. Inference
  retries classify failures through pydantic-ai's typed provider errors
  (`ModelConnectionError`, `ModelRateLimitError`, `ModelServerError`,
  `ModelUnavailableError`), which that release introduced; projects relock to
  pick up the floor.
- Agents: `InferenceModelUse.DECISION` is a new model use. Projects author the
  `AlterField` migration for `InferenceModel.model_use` before
  `makemigrations --check` passes.

## Unreleased — record access threads no database alias

- `AngeeModel.lock_for_record_access()` takes no `using` argument: an override
  drops it and locks its prerequisite rows through their managers. The
  `record_access_revoked` signal no longer sends `using`.

## Unreleased — django-zed-rebac 0.32

- In 0.32.2, frozen permission id sets compile with Django's compact subquery
  aliases, including recursive role helpers. This removes the repeated full-table
  names introduced in 0.32.0 while preserving id-set sharing and statement counts.
  SQL-text and planner-memory ceilings follow the measured reduction; content-hash
  id-set names remain.

- In 0.32.1, cross-row validation retains queryset-level bypass authority instead
  of losing it during save admission. Instance sudo audit deduplication follows
  transaction/savepoint rollback, so a rolled-back audit cannot suppress the next
  committed bypass entry.

- The base addon requires `django-zed-rebac>=0.32.2,<0.33` (GraphQL its
  `strawberry-django` extra, the dev group its `caveats` extra); the engine now
  requires `django-cte>=5.0.1,<5.1` (locked at the tested 5.0.1).
- Equal permission id sets across embedded SELECT scopes share one outer
  `WITH RECURSIVE`. The timeline opts both plain statement roots in through
  `rebac.share_id_sets`; each read remains one statement. Differently bound
  templates and writes retain nested definitions. Parametric embeddings use one
  application-clock instant per outer SELECT compilation.
- The library ships only its local backend: `rebac.Backend`, `SpiceDBBackend`,
  the `REBAC_SPICEDB_*` settings, `PermissionEvaluator.accessible()` and
  `RebacManager.raw()` are gone (`LocalBackend.accessible()` and the queryset's
  `raw()` stay). Base autoconfig no longer sets `REBAC_BACKEND`; the library's
  default selects the local backend.
- An actor-scoped read compiles to one actor-independent statement: arrows,
  subject sets and memberships are predicates (each id set one CTE), not reads
  decided beforehand. A scoped read is its revision witness plus one statement,
  and a fetched page adds one decision of its gated fields per distinct
  permission over the fetched ids.
- Inside a request's evaluator scope, a witness statement decides whether the
  actor holds a permission's constant arms (`admin->member`). An administrator
  then reads a statement of those arms alone. The answer is kept per request and
  database alias for a type none of whose decisions reads a model column, as the
  compiler's dependency graph decides; a type whose policy follows an arrow or
  subject set to a model-backed relation (here the field-backed
  `auth/user#person`) is decided again at each read. Each actor pays one witness
  per distinct arm per request on the console reads, and a member's statement
  leaves out the arms it does not hold. On the composed test host the admin
  transcript is 32 statements and 129 KB (0.30: 35 and 85 KB), the member's 32
  and 2,593 KB (0.30: 250 and 2,505 KB). The console ceilings and the statement
  pins are re-measured.
- Known limit: a statement materializes the id sets it reaches without the
  outer row restriction, so on SQLite a member's parts read of one message
  scans the thread, message, channel and integration tables beside its index
  seek on that message's parts; an administrator's read, of the constant arms
  alone, seeks only them.
- A related manager reads with its source instance's pinned actor (never its
  sudo pin) instead of the ambient actor, and `iterator()` streams chunk by
  chunk. A row list loaded for one actor and reused under another actor's
  context returns the first actor's relations; load the rows for that actor.
  That actor survives `system_context()`, so system work on an actor-bound row
  reads its relations through `<related>.system_context(reason=...)`;
  `Runner.advance` is the reference.
- REBAC's `relation_actor()` answers no actor inside `system_context()`, where
  0.30 answered the ambient actor. `held_permissions`, `permission_annotations`
  and `permissions_field` decide for the ambient actor they stamp, so a verb's
  admission inside a system block still sees that actor's own rights;
  `permission_expression(queryset, name)` follows the queryset's relation actor
  and projects nothing there unless the queryset is pinned.
- A queryset `sudo()` or `system_context()` entry writes its audit row at least
  once per actor and evaluator scope, however many querysets derive from it; a
  row in a transaction that rolls back does not count. An embedded one is
  audited when its enclosing statement executes, so its audit row follows that
  statement.
- Follow-up for REBAC 0.31.6: a `<related>.system_context(reason=...)` entry
  opened inside a `system_context()` block with the same reason is an entry of
  its own, so `Runner.advance` writes two `workflows.advance` rows and the map
  concurrency test bounds the settling item at 101 statements; the library is to
  fold it into the block's row. A queryset built inside a block and embedded
  twice in one statement (two `Subquery` uses) writes two rows; the release is
  to derive that capture entry from the block, so one block gives one entry.
  Outside an evaluator scope an entry is audited once per actor for its life;
  the same release is to audit it once per top-level operation there.
- GraphQL permission projections (`permissions_field`, `held_permissions`,
  `permission_expression` filter expressions) and `Membership.with_can_read`
  compose REBAC's native `permission_expression` instead of a correlated scoped
  `EXISTS`. Verb eligibility projections that combine a permission with row
  blockers keep their aggregate-scoped subquery, so hidden blocker facts stay
  inside it.
- Change-subscription payloads decide their changed gated fields through
  REBAC's batched field visibility: one statement per distinct
  `read__<field>` permission instead of one point check per field. A
  depth-uncertain field is redacted instead of raising.

## Unreleased — scalar foreign-key public ids

- `public_id_field("relation", nullable=False)` (`angee.graphql.ids`) projects a
  primary-key foreign key as a `PublicID` from the row's stored key, without
  loading or authorizing the target. `nullable` decides the GraphQL type; schema
  build refuses any other field and an annotation that disagrees with it.
  `actor_scoped_public_id` stays the owner when target readability must redact
  the reference. The `knowledge`, `storage_integrate` and `dashboards` schemas
  declare it in place of their hand-written resolvers.

## Unreleased — one page bound, reader batches and loading labels

Consumer-facing renames and removals, each with its replacement.

- GraphQL: `WorkflowRunType.graph_steps` is `graph_step_runs`; clients select
  the new field.
- `require_rebac_queryset(model, actor=None)` reads nothing, whatever the
  ambient actor or system context. Omit `actor` for REBAC's ambient scope; pass
  an actor to pin it.
- `ThreadedModelMixin.thread_attachment_role` is deleted; read
  `ThreadAttachment.AttachmentRole.CHATTER`.
- A related container declares `relatedCount: { models }` (was `count: { id,
  models }`); its container id is the server related count id.
- Loading owners take `loadingLabel`: `RecordHeader`, `Combobox.Status`,
  `PaneRowsSkeleton` and `SkeletonStatus` (was `label`), `DetailSurface` and
  `MetricStrip` (was `loadingMessage`), and `LoadingPanel` (was `message`).
- `useStorageActionRun` is deleted; storage verbs settle through
  `useBusyRun().settle(action, fallback, failureTitle?)`.
- `GROUP_ROWS_MAX_LIMIT` is deleted; `angee.base.pagination.MAX_PAGE_SIZE` is the
  one page bound. An explicit page size outside it (group rows' `limit`, inbox
  pages, provenance history) is refused as `BAD_USER_INPUT` through
  `valid_page_size`; first-N reads (timeline, removed tasks, people search,
  answered decisions, record runs) clamp to it.
- `angee.graphql.E003` (the in-memory channel layer beside a Celery broker) runs
  only for the serving commands (`serve`, `runserver`, plain `check`) through
  `SERVING_TAG`; build and data commands no longer meet it.

## Unreleased — the inbox reads conversations without record chatter

- `messaging/thread` has two read extension points. `read_granted` takes grants
  that hold for any conversation (channel, owner, shares, identity rule, admins,
  bound projects, selected groups); `chatter_read` takes a record's read of its
  own chatter thread (projects' `record_project` and `record_task`, knowledge's
  `record_page`). `read` unions both; the new `read_inbox` is `read_granted` alone.
  `messaging/message#read_inbox` withholds and keeps exactly as `read` does over
  the thread's `read_inbox`; message `read_granted` now holds only the message's
  own channel and share grants.
- `ThreadQuerySet.inbox()` and `MessageQuerySet.inbox()` select `read_inbox`, so
  every inbox read (results, groups, counts, search, navigator, transcript, feed
  and revalidation, the generic `threads` and `messages` lists) stops compiling the
  chatter arms. The inbox excludes record chatter, so its answers are unchanged.
  A verb that needs another action selects it after `inbox()`.
- A fragment that unions an arm straight into a thread or message `read` or
  `read_inbox` is refused by the security check `messaging.E003`. Consumers move a
  chatter-filtered thread arm from `read_granted` to `chatter_read`; grants for any
  conversation stay on `read_granted`.
- Operators re-sync the permission schema (`rebac sync`, which provision runs):
  messaging revision 23 (it also adds `messaging/part#read_inbox`, through which
  the inbox lists a part of a readable message), the projects fragment 9 and the
  knowledge fragment 3; a source host also syncs the `tests.extcontrib` fragment 9.
  Move every stored REBAC override on a thread or message `read` or `read_inbox`
  to that definition's `read_granted`, which both reads take. While one remains,
  or the store is not yet synced, the inbox reads threads, messages and their
  parts under `read` (`purpose_read_holds`): correct, without the narrower
  statement.

## Unreleased — dead subsystems removed with their schema

Each removal below changes composed models. A project removes the storage in its
own native migration history. No framework addon or consumer reads these facts.

- **Extraction correction.** The decision-backed correction writer and
  everything only it fed are removed: `ExtractionManager.prepare_correction_binding`,
  `revise_from_decision` and `reviewed_correction_authority`, `CorrectionBinding`,
  `CorrectionRef`, the outcome's `corrections` list with its readers
  (`Extraction.corrections`, `fact_correction`, `retained_corrections`,
  `reviewed_facts`), the helpers only the correction path read
  (`Extraction.reference` with its `ExtractionRef` value, `failed_at_inference`
  and `pointers.fact_pointers`), and the `Extraction.correction_decision`
  foreign key.
  Nothing called the writer, so no revision holds a correction; retained
  outcomes may still carry an empty `corrections` list that nothing reads.
  `Extraction.fact_authority` reports `source` or `unverified`. A project
  composing `angee.extraction` removes the column with
  `RemoveField(model_name="extraction", name="correction_decision")` in its
  `extraction` history; the drop guard admits the autodetected removal while
  the column is null on every row.
- **Notification delivery states.** No worker ever advanced or failed a
  notification, so its delivery lifecycle and diagnostics are removed:
  `ThreadNotification.NotificationStatus`, `notification_status`,
  `failure_type`, `failure_reason`, the `ix_thread_notification_pending` and
  `ix_thread_notification_failed` partial indexes,
  `ThreadNotificationManager.mark_failed`, `mark_failed_for_message` and
  `error_count_for_record`, and the queryset's `delivery_errors` with its
  `DELIVERY_ERROR_STATUSES`. GraphQL drops the three fields from both
  notification types and the `notification_status` filter field on
  `thread_notifications`, and the record chatter state drops `message_has_error`
  and `message_has_error_counter`; clients stop selecting them. A project composing
  `angee.messaging` removes the two indexes and three columns (`RemoveIndex` and
  `RemoveField` on `messaging.threadnotification`); the drop guard admits the
  autodetected removal while every row holds `ready` and empty failure text.
- **Intake channel triggers.** Channel intake offered `reaction`, `mention`
  and `command` beside `all_messages`; none of the three ever captured a
  message. They go with `ChannelIntake.IntakeTrigger` and the `intake_trigger`
  field, since its one remaining value carries no fact: a channel with an
  `intake_queue` captures every ingested message. GraphQL drops
  `ChannelType.intake_trigger` with its update input and its filter, sort and
  group axes, and the demo channel row drops the key. A project composing
  `angee.intake` first clears `intake_queue` on channels whose stored trigger
  is `reaction`, `mention` or `command`, so they keep capturing nothing, and
  flushes deferred foreign-key checks (`connection.check_constraints()`); then,
  in the same `messaging` migration (the field folds into the channel table), it
  removes the column with `RemoveField(model_name="channel", name="intake_trigger")`.
  The drop guard refuses the autodetected removal while any row holds a retired
  value.

## Unreleased — jj workspace slots in the templates

- The `src` workspace template takes `slot_mode` (`worktree`, the default, or
  `jj`) as the mode of its three code slots, so a stack can cut them as jj
  workspaces of the shared source caches. The work-state slot still names no
  mode: the stack's `vcs` decides it, and `ws sync-base` and `ws push` skip it.
- `stacks/dev` takes `vcs` (`git`, the default, or `jj`), rendered as the
  manifest's `vcs`, and `slot_mode`, rendered into `workspace_defaults` for
  `workspaces/src`. The defaults render neither key, so a stack that sets
  neither renders as before. `stacks/local` declares no workspace and takes
  neither. [Templates](templates/README.md#cut-workspace-slots-as-jj-workspaces)
  has the requirements and update procedure: angee 0.26.0 applies `vcs` while
  pinning existing unspecified-slot modes; angee 0.25.0 requires the documented
  manual transition.
- The `src` workspace's `AGENTS.md` no longer says the operator does not manage
  jj. It tells a slot the operator cut as a jj workspace by its `.jj`, sends
  agents to `angee ws create` only when the stack's `workspace_defaults` sets
  `slot_mode: jj`, and carries the agent contract for a jj slot: write through
  jj only, never rewrite commits a pushed bookmark or the parent ref reaches by
  hand, never pass `--ignore-immutable`, sync through `ws sync-base`, publish
  through `ws source publish` or `ws push`, and files jj will not snapshot
  block both. An existing workspace picks it up on `angee ws update`.

## Unreleased — addon distribution metadata

- Each addon declares native package metadata and requirements in its own
  `pyproject.toml`, with one `angee.addons` entry point. Capability tables remain
  package data. The repository root is a uv workspace and metadata-only collection.
- Dependency installation, explicit operator `compose`, and Python archive
  builds are separate operations. Native migration history remains reviewed,
  committed project source.
- Framework and addon wheel publication targets the configured private Python
  index because declared dependencies include pinned fork URLs.

The unreleased sections describe the current 3.0 framework cohort. Released
and dated sections below preserve their historical contracts.

## Unreleased — foundation and composition (breaking; 3.0)

- **The `angee.base` foundation addon lives under `src/angee/base/`.** Models, mixins, fields,
  managers, querysets, indexes, validators, identity and permission integration
  belong to that addon. The frozen historical REBAC helper is removed. Existing databases
  must first pass through compatible code that still supplies these imports and
  `reset_migration_history`; finish migrations and collapse their composed history
  before taking the removal. Update pinned addons too: older messaging bridges
  imported `SourceKind` through `angee.base.mixins`; its public owner is now
  `angee.provenance.statements`. Follow the [upgrade cutoff](docs/composer.md#foundation-and-provenance-upgrade-cutoff).
  The persisted Django app label `base`, tables and content types keep their names.
  Retired migration modules must be absent from tracked project history before
  this upgrade; the cutoff guide owns the required transition.
- Mixed root modules are decomposed by responsibility: `ImplClassField` lives in
  `angee.base.fields.implementation`, implementation configuration in
  `angee.base.implementations`, model defaults in
  `angee.base.models.implementations`, and independent hook resolution in
  `angee.base.hooks`. JSON Schema validation and shared schema contracts belong to
  foundation; workflow schema algebra belongs to workflows. `angee.data` is
  retired: GraphQL owns resource projections, and foundation owns the shared
  FormSpec schema consumed by Python and generated frontend validation.
  The superseded top-level modules and compatibility aliases are removed.
- Generic compilation, component ordering, receipts, migration coordination and
  provisioning belong to published `django-compose`. The project owns native
  `settings.py`, `settings.yaml` and `manage.py`; `angee.base.conf` supplies Angee
  defaults. The in-tree compiler and manifest/dependency compiler are removed.
- Jobs/Celery and web builds have optional addon owners. `angee.server` owns HTTP
  policy; base owns reusable Django primitives. Capability adapters contribute
  through native compiler contracts. Stack templates can omit Celery services
  with `enable_celery=false`, independently of app activation.
- Provenance lives entirely in the `angee.provenance` addon. Context, evidence,
  history and statements leave the model foundation; callers import their addon
  owners and explicitly opt into captured managers. The native Django capture
  engine is separate from the `provenance.contrib.angee` permission and replay
  adapters. HTTP, task, command and test boundaries are installed by the addon.
  GraphQL hosts without provenance retain native CRUD and expose no creation
  idempotency argument. See the [addon contract](src/angee/provenance/README.md).
- Model references follow Django's explicit model-package exports:
  `RecordRefMixin` and `DerivedFrom` are imported from
  `angee.base.models.refs` and `angee.base.models.derived`;
  reference discovery and deletion hooks live in `angee.base.models.refs`.
  `angee.base.refs` owns pointer identities and `RetainedGenericRelation`, with
  historical migration imports handled by the upgrade cutoff. Test
  callers use `rebac.testing.install_schema(..., backend=...)` directly instead
  of the removed `angee.testing.permissions.install_permission_schema` wrapper.
- One spelling of ownership: `OwnerMixin.owner` is the column and a relation named
  `owner` is that column (`angee.E045`). `AgentSession.owner` is `user`,
  `parties.Handle.owner` is `controller` (GraphQL `AgentSessionType.user`,
  `HandleType.controller`); `Integration.owner` and `WebhookSubscription.owner` are
  nullable `retained_set_null`; `owner` lands on `Party`, `Folder`, `Circle`,
  `Workflow` and `ExternalAccount` (the external-account `#owner` tuples fold into the
  column). Each addon contributes its cutover through `compose_migrations/<label>/`;
  Django project history retains the materialized migration.
- `client_creation_key` is `idempotency_key` on every create argument and on the
  GraphQL wire; the conflict error keeps its code. A changeset claim owns create
  replay, so a kept database retires the stored creation keys through declared
  cutovers: `retire_creation_keys` in dashboards, knowledge, messaging and projects
  (with `ProjectSetupReceipt`), proposals `retire_clarification_keys` and provenance
  `retire_idempotency_key`. Intake retires `Need.original_task`; posts
  `single_scope_quota` deletes the legacy and spender-less quota rows the required
  `integration` and `scope` cannot hold.
- Workflows: extension points (`workflows.workflowextension` resources add steps to
  a published workflow at `after_<node>` / `before_<node>`), `blocks` tokens with
  `WorkflowRun.objects.blocking(record, action)` evaluated as policy, blocking runs,
  `reap_interrupted()`, page references on decisions.
- Decisions on records; a record call may name required arguments (binding is the
  check).
- One provenance surface: the `record#activity` container and the facts line
  (`context_fields`), `attentionColumn`, lock marks for redacted fields, the Sources
  aside with the provider `source_url` hook; see the `@angee/ui` changeset.
- `FileType.permissions` (verbs contributed to `storage.File#actions` gate on it).

## Unreleased — sync provenance and native history (breaking; 3.0)

The retired names and their owners are listed below; the upgrade path for a
stack that keeps its database follows the [upgrade cutoff](docs/composer.md#foundation-and-provenance-upgrade-cutoff) (a stack with re-importable data starts a fresh database).

- A record's facts are statements: who asserted a value, from what evidence,
  through which link. `StatementsMixin` and `Aspect` (`angee.provenance.statements`)
  declare them, `StatementManager` resolves the winner, and every write runs
  under a provenance changeset. Parties, circles and spaces memberships,
  addresses and relationships carry statements instead of `source` columns.
- django-reversion is retired. `RevisionMixin`, `revisioned_fields`,
  `angee.graphql.revisions` and each resource's `<resource>_revisions` root are
  removed, and every stored revision is dropped by ruling. Models keep native
  history; provenance serves it through `history(model, id)` and
  `revert_changeset`, and shows it as the form's History tab.
- Parties: `PartyHandle`, `MergeVeto`, `LinkSource`, `ScoredLinkMixin` and the
  CardDAV columns on `Party` are folded into statements and record links, then
  retired by a declared chain that preflights every refusal before it drops.
  `Party.merge_into` and `PartyManager.merge` give way to the shared record
  merge (`angee.base.models.merge`).
- Integrate: `RecordRevision` and `SyncDiscrepancy` are retired. A link's
  observations are its native history and a sync failure is a state of its link
  (`SyncFailure`, `LinkStatus.FAILED`); `SyncStream.authoritative` marks a
  system of record.
- Removed ahead of the 0.4.0 window they were promised (ruling 2026-10-18):
  the `DerivedFromRelation` alias of `angee.base.refs.RetainedGenericRelation`
  and `@angee/ui`'s `RevisionsTab`.
- GraphQL removals: `confirm_party_handle`, `dismiss_party_handle`, `veto_merge`,
  `duplicate_party_candidates`, `Party.party_handles`, `Person.folder`, the
  `source` field on circle members, relationships and space memberships,
  `resolve_sync_discrepancy`, `retry_sync_discrepancy`,
  `SyncStream.open_discrepancy_count` and the record link hash bases.

## Unreleased — native permission integration

- Permission integration uses native `django-zed-rebac` managers, queryset gates,
  schema checks and GraphQL extensions. Owning addon requirements and the native
  lockfile identify the reviewed dependency; generated addon dependency groups
  and the `angee build` repair step are removed.
- Since 0.27.1, read guards refuse filtering or ordering
  on a gated field under an actor; filters and sorts over a gated field use the
  queryset's `readable(field)`, and Angee's `gated_field_expression` is removed.
- IAM declares its deliberate disclosures in Zed: filtered constants on the
  account's own `is_active` and `is_staff` back `active_account` and
  `nonstaff_account`, each within the reach of the sites that use them, which
  pickers, preview and account protection match by primary key. Pickers leave
  inactive accounts out without showing their state; protection counts staff,
  and any account outside the verbs' reach, as protected.
- `AngeeModel.validate_unique` and `validate_constraints` compare against every
  row under a named system context when the row's save would pass its REBAC
  gates (REBAC's `check_save`, decided once per `full_clean`), leaving out the
  fields a saved row's actor may not read; before, an actor-scoped check saw only
  readable rows. `OwnerMixin`'s owner default is held for the checks.
- `AngeeModel.system_queryset` returns a permission-naive default queryset as it
  is, and GraphQL filter expressions take their output fields from it.

## Unreleased — record tasks (breaking; 3.0)

Upgrade steps: see the [upgrade cutoff](docs/composer.md#foundation-and-provenance-upgrade-cutoff).

- Record activities are record tasks: a `projects.Task` whose `subject` is a
  record's chatter attachment, typed by `projects.ActivityType` (moved from
  messaging with its public ids). A record task answers to its record alone, in
  the permission schema: a record type takes tasks once its owner declares
  `record_read` and `record_write` on `messaging/thread_attachment`.
- The cutover, `projects:record_tasks`, is irreversible: it converts every
  activity and drops messaging's `ThreadActivity` and `ActivityType` tables,
  holding the task tables' lock throughout. projects refuses the `migrate` run
  that would apply it while activities sit on undeclared record types.
- GraphQL removals: messaging's activity types and nodes, `activity_agenda`, the
  record activity mutations and the activity fields on the record-thread
  payload; `promote_thread_activity_to_task` and `Task.converted_from_activity`.
  Record tasks use `record_tasks`, `schedule_record_task`, `log_record_task`,
  `complete_task(id, feedback)` and `drop_task`.
- Messaging's change-history tab is `messaging.history` (placeholder
  `chatter.history`); the former ids stay as `?chatterTab=` aliases until 0.4.0.
- The composer's drop guard refuses an autodetected `DeleteModel` whose table
  holds rows, except rows the retiring addon's resource files seeded once it
  names the model under `retired_models` in its `pyproject.toml`
  `[tool.angee.resources]`.

## Unreleased — in-process agent sessions

- Integrate feed identity discovery, committed live horizons, quota reply
  reserves and serialized webhook landing with the shared stream and delivery
  owners. Held replies exclude further answers under the comment lock; reply
  hold and delivery settlement remain operator and messaging concerns.
- Publish workflow resource references through the resources ledger. Narrow
  agents can persistently opt out of the generated resource-reader role.
  Administrators own the reader opt-out; `resync_tool_grants` rebuilds every
  agent's membership from its current policy, readiness and runtime.
  Knowledge can ensure role-bound memory pages and create pages through
  vault-scoped GraphQL tools. One canonical `post_delete` teardown removes
  committed bindings and trashes only memory pages no other memory binding uses.
  Binding admission and teardown serialize on the page after the canonical target.
  A re-synced message without a receipt time keeps its stored `received_at`.

- Resource selection follows committed routes and its effective filter scope.
  Bulk actions receive retained `selectedRows` snapshots and explicitly declare
  whether they replace generic delete. Nonselectable views suppress selection in
  render. Held actions read one console `action_selection_limit` and clear settled
  ids for revision-aware reselection. Tags owns the independent record-tag widget.
  Integration models project and enforce `can_connect`, `can_resume` and
  `can_retry_discovery`; discovery owns a saved feed's read-only display name.

- Restore workflow conversations on persisted ACP turns with status-only
  watches, expiring delivery recovery, a deadline starting at claimed-turn observation,
  operator retry and explicit close. Terminal transitions close created sessions
  and cancel run-created turns on continued sessions. Deferred cancellation
  waits for its task to own the run locks; cleanup failures stay in receiver savepoints.
  Compose comment responding through `workflows_posts`: check the posts-owned
  reply predicate, prepare answer text with an agent, and schedule through
  the message owner's creation-key replay. The workflow principal holds reply
  authority; the responding agent receives only read tools and record access.
  Prompt bindings carry JSON only; publisher authority still gates agent calls.
  Builtin tool catalogue sync provisions its server and binds resource handles
  before agent tool selections resolve, without demo resources.
  Knowledge search groups bounded readable vaults by backend; lexical search
  scans each group once and MCP exposes `list_vaults`.
- Enforce transition-owned state assignment guards on composed concrete models,
  including inherited and deferred fields. Reload through `AngeeModel`, and copy
  loaded persisted row values through `StateTransitions.copy_persisted_state`.
  Remove `lifecycle` from agent generic update and insert inputs; lifecycle
  changes use the owning actions, and the statusbar is read-only.
- Run one persisted chat turn per Celery task through the agent's runtime and
  service-user actor. Model verbs own start, post, Stop, Close, streamed updates
  and settlement; a partial unique constraint permits one active turn per
  session. Add the `call` permission and console session mutations. Deprovision
  closes sessions, and retained sessions block agent deletion.
- Replace the session heartbeat with a bounded runtime deadline. Jobs owns the
  worker time budget shared with workflows. Chat has no automatic retries or
  crash recovery; Stop releases a stranded turn. Tool approval requests fail
  readably until session approvals are implemented.
- Protected relations now report readable GraphQL deletion errors. Sessions
  can be deleted after their active turn is stopped; streamed batches emitted
  before Stop is observed remain in the transcript.

## Unreleased — workflow and integration upgrades

- `angee.refs.canonical_record_target`, `canonical_record_model` and
  `CanonicalRecordTarget` are removed: a polymorphic edge stores
  `rebac.generic_target`, and model-level canonical projections read
  `rebac.field_backing.canonical_model`. Currency-rate contexts, resource
  `RecordRefField` imports and tag assignments key their target the same way and
  refuse an untyped row with `ValueError` (a currency-rate context reports it as
  a `ValidationError`). The two edges that name rows outside REBAC — a chatter
  thread on an ungated host and an import record link to a plain domain sink —
  store `angee.base.refs.generic_pointer_target`, the library's identity for a
  gated row and Django's own for an ungated one. Consumers that imported the removed
  helpers move to `rebac.generic_target` (an instance's stored content type and
  id) or `rebac.field_backing.canonical_model` (a model's canonical class,
  `None` when untyped).
- Workflow subjects, step records, step watches, decision records and extraction
  sources stay owner-authorized evidence edges: their access follows the run,
  decision or extraction, and the owner's admission gate (`readable_records`,
  the read-scoped lookup) checks the record at write time, so they take no
  per-type target relations. They now store `rebac.generic_target` and refuse an
  untyped record with `ValueError`.
- Messaging thread attachments authorize their record in the REBAC schema: the
  app that owns an attachable record type declares one `target`-backed relation
  for it and unions its write into `messaging/thread_attachment`'s `target_write`
  (projects: tasks and projects; proposals: rounds; messaging itself: files; the
  notes example: notes). A source conversation is bound to a record under the
  actor with write on the record and, through the library's backed-edge gate,
  write on the thread, and unbound by the record's or the conversation's
  writers; a record type no relation names is refused, and an untyped record
  raises `ValueError`. The target arms reach source edges only (a const relation
  on the row's role): the chatter edge remains the record's own thread,
  materialized by its verbs under system context, with access riding on the
  thread as before, and an ungated host keeps its own concrete content type
  (`angee.base.refs.generic_pointer_target`). The edge's `create` no longer
  admits every authenticated actor.
- Project links, project resource bindings and portfolio health reports
  authorize their target in the REBAC schema: each edge's per-type relations are
  backed by its `GenericForeignKey`, rows are stored at `rebac.generic_target`
  and created, written and deleted under the actor. The three edges derive
  their accepted target types from the schema
  (`RecordRefMixin.declared_target_models` / `validate_target`) instead of
  listing them; a binding requires share on the project and the resource type's
  own grant authority on both create and delete, stores a channel as its
  integration (still the only integration kind a project binds), and is never
  retargeted; a report's target stays immutable. `ProjectBinding.objects.bind`
  and `unbind` replace `angee.projects.access.bind`/`unbind`. Removed:
  `LinkManager.TARGET_RELATIONS`/`target_relation`/`target_model`
  (`Link.declared_target_model` replaces the last), `UpdateManager
  .TARGET_RELATIONS`/`target_relation`/`target_model`, the update's stored
  relationship tuples, `ProjectBinding.allowed_target_models`, its
  `ProjectBindingQuerySet` and the hand-rolled binding save/delete gates;
  `projects.access.require_binding_access` now guards only the project's home
  folder.
- Tag assignments are authorized by the record they tag. The app that owns a
  taggable type (one composing `TaggedModel`) declares its `target`-backed
  relation on `tags/tag_assignment` from its `permissions.extends.zed`, unioning
  the type's read and write into `target_read` and `target_write`: projects
  (projects, tasks), storage (files), knowledge (pages), messaging (threads,
  messages) and parties (parties, handles). Adding or removing a tag, through a record's
  save or the `tag`/`untag` verbs, needs write on the record and runs under the
  actor; an assignment is read by whoever reads both the tag and the record.
  `tag`/`untag` no longer require the `tags_admin` role (`TagsAdminPermission`
  is removed): tag administrators curate the vocabulary, not records. A type no
  relation names cannot be tagged under an actor, so consumers that tag other
  record types compose `TaggedModel` and declare the relation;
  `TagAssignment.check` (`tags.E001`–`tags.E003`) fails startup when the
  composed models and the declared relations differ. `parties.Party` is
  taggable: the party, person and organization console nodes and inputs carry
  `tags`, and `angee.parties` depends on `angee.tags`. `parties.Handle` is
  taggable too: its console node reads `tags`, and since its resource takes no
  writes, the `tag`/`untag` verbs write them. Resource metadata now
  lists `tags.TagAssignment` in `recordEdges` for taggable types.
- File attachments and knowledge record bindings authorize their target in the
  REBAC schema (django-zed-rebac 0.26.0 relations backed by the edge's
  `GenericForeignKey`) and are created and deleted under the actor. The app that
  owns a target type declares it from its `permissions.extends.zed`, unioning the
  type's arms into the edge's `target_write` (and, for bindings, `target_read`);
  projects declares tasks (files, bindings) and projects (bindings). A target
  type no relation names is now refused under an actor: consumers that attach
  files to, or bind knowledge to, other record types must declare them. An
  untyped target raises `ValueError` from `rebac.generic_target`, under system
  context too. `FileAttachmentManager.authorized_target` is removed.
- Hasura input-extension values that name no model field reach their model: the
  default write backend writes the row, then passes them to the cooperative
  `AngeeModel.apply_input_extensions` hook in the same transaction (create is now
  atomic too; nested line rows included). A value no consumer takes fails fast
  instead of being silently dropped. Resource metadata marks every final
  insert/set input field creatable/updatable, extension fields included, so
  IAM's user `password` now reads as creatable.
- Integrate's outbound HTTP (`PinnedTransport`, `HttpClient` and the OAuth
  client) runs on httpx2/httpcore2 and requires Authlib >= 1.8.0, whose
  `OAuth2Client` is an `httpx2.Client`; under Authlib 1.8 the httpx transport
  failed every token exchange, refresh and revocation, including OIDC sign-in.
  `HttpClient` responses, transport factories and transport errors are httpx2
  types, so callers catching httpx errors or injecting `httpx.MockTransport`
  move to httpx2, and downstream repositories regenerate their addons
  dependency group.
- Resource seeds apply transition-owned state only when creating a target;
  updates and adoption preserve live state. Initial state is excluded from
  content hashes, so changing it alone does not trigger re-import.
  Unchanged seeds with state-inclusive ledger hashes skip import and migrate
  only their ledger hash. Seeds whose values or keys changed still import once,
  overwriting operator edits to other fields they name while preserving live
  transition-owned state.
- Workflow IO steps commit claims before their bodies, fence results, reap
  deadlines and retain effect markers. Operator actions recover failed or
  stalled work with explicit duplicate acknowledgement when required. Add
  checkpoint paging, artifact references and reprocess provenance, plus native
  read-only GraphQL execution resources. Cancel bounds its lock wait.
  Failed runs preserve unfinished siblings; retry replans their joins from
  retained rows, including IO results received after the failure.
  Delete the frozen workflow migration compatibility modules and default
  callable under the authorized history reset.
- Project settings composition publishes only after success. App-config
  discovery no longer evaluates money settings prematurely, preserving the
  original dependency error during Django startup.
- `AngeeModel.require_access(permission, actor)` resolves the explicit requester,
  then the instance's pinned actor, then the ambient requester, and returns the
  authorized actor. Missing actors fail closed outside system scope.
  `_require_record_access` remains a temporary alias for downstream callers;
  remove it after those callers migrate. Workflow starters can read identities
  and versions and operate their own runs; monitoring other runs and reading
  unpublished drafts requires workflow monitoring access. Engine rows remain
  admin-write only; synchronize workflows REBAC schema revision 13.
- `RecordRefMixin` uses the model's single native generic foreign key;
  `record_ref_field_prefix` is removed and rejected by Django system checks.
  `ImplBase.parse_value(value, type_, path)` owns typed error paths;
  `parse_config` returns the model and `normalize_config` serializes it.
  Step callers use `parse_config` directly.
- `AutoConfig.apply_installed` composes installed app declarations for bare
  hosts, with environment selection at the owner. Resource managers expose
  `load_xref` for one declared row through the native import pipeline.
  Workers default to `CELERY_WORKER_PREFETCH_MULTIPLIER=1`.
  The operator GraphQL endpoint keeps its same-origin `/operator/graphql`
  default; explicitly configuring `None` enables daemon URL derivation.
- Rebuild the workflows engine around immutable published definitions,
  actor-scoped runs, database steps and durable attempts. Remove the workflows
  web fragment. The `workflows_agents`, `workflows_extraction`,
  `workflows_integrate`, `workflows_messaging` and `workflows_parties` satellites
  compose the rebuilt step, settlement and trigger owners. Remove
  `ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT`, `ANGEE_WORKFLOW_SUBJECT_SETTLERS`,
  `ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES`, `ANGEE_EXTRACTION_PROFILE_CLASSES`,
  `ANGEE_EXTRACTION_MAX_BYTES` and `ANGEE_EXTRACTION_TIMEOUT_SECONDS`.
  Replace the beat entries `workflows.decisions`, `workflows.reap`,
  `workflows.publish_dispatches`, `workflows.schedule_triggers` and
  `workflows.sweep` with `workflows.tick`.
  `angee.jobs.enqueue_task` sends on transaction commit, immediately when no
  transaction is active, and no longer accepts `eta`.
- Beat keeps its schedule in the database (django-celery-beat) through
  `angee.jobs.scheduler.DatabaseScheduler`: code-declared `CELERY_BEAT_SCHEDULE`
  entries are written at startup and rows no longer declared are pruned.
  `CELERY_BEAT_SCHEDULE_FILENAME` is gone; delete stale
  `ANGEE_DATA_DIR/celerybeat-schedule*` files. Stack templates run beat inside the
  shared worker (`worker --beat`) and no longer render a `celery-beat` service.
  Existing stacks keep working (their `celery-beat` service uses the database
  scheduler after the next provision migrates it); when upgrading, remove
  `celery-beat` and add `--beat` together, never one without the other, or
  re-render the stack passing back every non-default answer.
- Intake captures needs again: `Message.transport_channel()` owns resolving a
  message's concrete `messaging.Channel`, shared by delivery and intake.
- Live authored reads coalesce subscription pushes: a change cancels matching
  in-flight requests at once and each affected read refetches once per burst.
- Generated-runtime rebuilds reset once, preserving migration history and
  warning only for labels outside the current composition. Remove unused
  runtime wrappers; materialization requires
  the composed app registry. Project authors run `makemigrations` explicitly
  and review the resulting history; provision only checks and applies it.
  Generated-tree pruning removes reported empty owned directories after their
  contents, so reconciliation converges while preserving migration history.
- Retained collections use one model-labelled `ValidationError`. Lease updates
  and validated retention batches use the public, framework-protected
  `owner_update` and `owner_bulk_create` paths that preserve downstream guards;
  hierarchy path maintenance uses the same `owner_update` contract. Fixture,
  recovery and dispatch collections consistently reject generic inserts.
  Implementation-field checks share registry resolution
  and reject mismatched implementation keys.
- Provenance autoconfig enables simple-history's native text change-reason setting,
  replacing the private field-factory override. This intentionally applies
  project-wide, including consumer and third-party `HistoricalRecords` using the
  default change-reason field; existing default `CharField(100)` histories need
  schema migrations to `TextField`. Explicit field declarations remain in force.
  System check `angee.E021`
  rejects hierarchy queryset ordering that would bypass another write guard.
- Storage resolves missing drive/backend relations within one system context,
  reusing an active context; cached storage access performs no queries or audit
  writes.
- Currency projection metadata is owned by `angee.graphql.data.resource_fields`;
  import `MONEY_CURRENCY_FIELD_METADATA_KEY` there.
- Add opt-in `angee.workflows.testing` and `angee.integrate.testing` apps for
  addon source test models. Foundation owns the generic
  `angee.base.testing.fixtures.composed_tables` fixture, using pytest-django's native
  table setup and cleanup with REBAC synchronization. Workflow drivers live in
  `angee.workflows.testing.drivers`; duplicated test models, table fixtures and
  suite-local driver forwarding imports are removed.
- Workflow definition validation asserts JSON Schema formats for inputs and
  binding literals.
- Foundation declares the `jsonschema[format-nongpl]` and `referencing`
  dependencies. `angee.base.validators` owns declaration and format validation;
  `angee.base.schemas` owns shared references and form contracts;
  `angee.workflows.schemas` owns workflow schema algebra.
- Row-lock callers consistently use `lock_if_supported`, which delegates write
  routing and backend support to Django.
- Slack rate-limit retries use the SDK's shared attempt budget, explicit polling
  deadlines, and the existing sleep cap. Integration HTTP clients expose a
  transport factory for injected test transports.
- Translation fallbacks use native i18next plurals and interpolation, including
  locale-specific zero-count selection unless the fallback declares a zero form.
  Test fixtures use the shared initializer, and runtime translation types match
  i18next's language contract without instance casts.
- Row and batch resource target resolution share `angee.base.identity` and its
  field-owned public-ID decoding with Django's `in_bulk`; row adoption and hash-skip state
  stay on the resource and are reset before import.
- `angee.base.schemas.references.LocalSchemaReferences` owns root-local JSON Schema
  pointers and anchors through `referencing` for workflow structural proofs and
  implementation forms; remote references and nested resource scopes remain
  unsupported.
- IMAP sample preview fields and validation compose react-hook-form with
  `DialogForm`; previews start directly from validated submissions and validation
  messages describe affected inputs. The backend remains the sample-limit authority.
- CardDAV uses `HttpClient.request(same_origin_redirects=3)` for bounded redirects
  that retain the request method, body and credentials only on the same origin.
  Photo downloads share that origin comparison; URL-gate validation errors retain
  their original type, while origin-changing redirects become `CardDavError`.
- Messaging adapters import `mapping`, `millis_to_utc`, `sequence` and `text` from
  the public owner `angee.messaging.identity`. Its retired `_wire` compatibility
  module is removed after callers have moved.

- Remove write-alias threading and custom `using=` parameters, including the
  `using` payload on `change_published` and `file_finalized`; Django routers own
  database routing. Frozen migration helpers retain Django's connection alias.

- Existing stacks must build and migrate on the previous release line first,
  at least through source revision `0a55a6fb`. Framework runtime history is
  carried forward, including materialized bodies from retired declarations and
  the frozen `angee.historical_relationships` import module. Never empty
  `runtime/*/migrations` on a stack whose database is carried forward. Stacks
  depending on the retired `workflows_ocr` label must remain at that upgrade floor
  until their migration graph has a supported transition.
- The guarded storage runtime migration converts the optional smart-kind
  column from an empty string to NULL while preserving rows and replacing
  affected constraints.
- Run `rebac sync` after migrate for the new dashboards `shared` relation and
  shared-reader reconciliation. Regenerate GraphQL SDL and clients.
- `StateField` `db_index=False` opt-outs now round-trip through migrations;
  schema autodetection can remove indexes previously retained by incorrect
  field serialization. Optional states must declare `null=True, blank=True`;
  concrete models with blank non-null states fail Django's field checks.
- The integration ownership guard now belongs to its consumer addon. The
  framework no longer supplies `angee.integrate.ownership`; consumers own their
  ownership policy through the declared integration contract.

## Unreleased — theme addons and Appearance

- The original blue palette is presented as Default while retaining its
  `angee.stock` preference key. New model-free Angee and Fyltr identity addons
  provide light and dark schemes derived from their public web palettes.
- Themes are ordinary addons with pure headless definitions, optional scoped
  CSS/assets and bounded versioned options. The composer rejects duplicate IDs
  and codegen emits a deterministic catalogue and theme stylesheet.
- Default, Angee, Fyltr and Brand can be personalized from their authored base
  with shared identity, surface, app-rail, status, logo, typography, corner,
  density and elevation controls. The saved inputs regenerate coordinated light
  and dark semantic tokens; restoring defaults returns to the addon's exact
  authored layers.
- The host injects its validated appearance default before application code.
  Signed-in users persist theme, color scheme and supported inputs in the IAM
  preference document through one shared Appearance provider.
- `data-color-scheme` and the color-scheme APIs are canonical. The old
  `data-theme=light|dark` attribute and `lib/theme` exports remain compatibility
  mirrors for one deprecation cycle and will be removed at the next breaking
  release.
- Existing projects must update their project template and run the normal
  `angee build` plus web build so the explicit CSS entry, generated catalogue,
  virtual host defaults and synchronous head bootstrap are present.

## Unreleased — Inbox query performance

- Personal inbox feeds deduplicate only when text search introduces multiplying
  joins. Group pages obtain their header count from a SQL window; distinct root
  totals remain exact when content or sender identities overlap.
- Disjoint message and sender groups derive root totals from the same grouped
  query. Navigator names reuse the authorized handle-name expression directly;
  conversation titles load only for the returned page. Recipient and shared
  content groups retain distinct totals across overlapping buckets.
- Nexus result groups load previews on expansion through the native ListView.
  Source accounts project only eligible channel IDs; relation choices use bounded
  existence checks instead of scanning the full readable quote graph.
- Explorer search combines indexed text matches and attachment filenames as
  distinct message-ID candidates, retaining phrase, role and access boundaries.
- Authored reads share in-flight requests across pane remounts when the data
  provider ignores cancellation. Providers that consume the signal keep native
  cancellation; explicit invalidation and live-connection catch-up remain intact.
- Require django-zed-rebac 0.16.3 for lazy SQL authorization of acyclic,
  non-caveated permissions. Shared access and revocation retain the engine's
  semantics; recursive and caveated permissions keep its conservative fallback.
  Grant expiry uses the application clock consistently; tuple-derived grants
  for non-native identities use the queryset's database alias.

## Unreleased — upstream reuse

- REBAC now owns schema introspection/rendering/extensions and scoped queryset
  projection. Hasura/aggregates own JSON grouping, public-ID key hooks, per-resource
  regex lookups and named generated resource members; Angee copies/global patches
  are removed. Exact feature commits are pinned for review; no package release
  is implied by the development version numbers.
- In-process inference consumes native Pydantic AI model/messages/responses and
  usage. `InferenceRequest`, `InferenceResponse`, `ChatAPI`, the runtime provider
  switch and separate vendor chat serializers are removed. See
  [inference migration](docs/howto/inference.md) for the new public API, credential
  lifetime.
- Imports retain native Tablib datasets through import-export's lifecycle,
  removing `ResourceRow` and its dataset reconstruction. Xref resolution and
  adoption coercion happen once through native fields/instance loading. Rows,
  M2M links, ledger and grants roll back together.
- Forms and tables use native RHF and TanStack state with Refine core transport;
  the Refine table and RHF wrappers are removed. Authored reads expose native
  Query controls and share a provider/document/variables cache; operator snapshots
  update it directly. Resource metadata uses Valibot-inferred wire types;
  FormSpec types and standalone browser validation are generated from the
  foundation-owned JSON Schema.
  [Frontend migration notes](docs/frontend/upstream-reuse.md) record changed APIs
  and preserved URL/favorite contracts.
- Two deliberate gates remain: the bounded import constraint evaluator cannot
  be replaced by Django's fail-open `Q.check`; infinite retained history cannot
  drop its archive until authoritative cursor/revocation reconciliation exists.

## 0.2.0 — 2026-08-24

- Addon dependency facts are unbundled from the framework wheel: folder-addon
  manifests own them and the composer projects their compiled dependency group
  into each host.
- The machine-readable data-surface contract now lives in the Strawberry-free
  `angee.data` core package for use by GraphQL, MCP, and other producers.
- The GraphQL runtime moved from the wheel to the `angee.graphql` folder addon
  in `angee-base`, taking the Strawberry stack and its dependency pin with it.
- The remaining framework apps are plain always-on Django core; their former
  addon manifests and generated entry points are gone.
- Distribution metadata, long-description links, source inclusion, and trusted
  publishing plumbing now produce a PyPI-clean wheel and source distribution.

## 2026-07-15 — Exact grouped pagination totals

- Groupable Hasura resources now advertise a `groupsCount` root alongside
  `groups`, including Angee's JSON-path grouping adapter. The scalar root
  delegates to the aggregation owner's database-side count and shares the row
  query's filter, dimensions, and `HAVING` semantics without inheriting its
  order, limit, or offset.
- Generated group operation documents select the row window and exact total in
  one GraphQL request. The Refine boundary requires a numeric `totalCount`, and
  grouped list/facet pagination no longer guesses cardinality from page length.
- The minimum composition stack is now `strawberry-django-aggregates 0.10.0`
  and `strawberry-django-hasura 0.6.0`.

## 2026-06-07 — Storage base addon (backend)

`angee.storage` lands the file domain as a base addon: `Backend` (credentialed
backend rows, env-ref config expansion, cached instance resolution), `Drive`
(addressable volume + content-addressed `object_key`), `Folder` (real trees and
per-user smart folders; Trash ships), `MimeType` (master-tier taxonomy seed),
`File` (per-drive content-hash dedup, DRAFT → READY/FAILED lifecycle,
soft-delete to Trash, `purge`), and `FileAttachment` (polymorphic edge).
Verified end to end: 264 backend tests, mypy/ruff/vulture clean, `angee build
--check` + `schema --check` green, and a live proxy upload through the composed
HTTP stack. Durable decisions:

- **Creating a File is not uploading — bytes are a pluggable source.** The
  factory `File.objects.draft(...)` reserves a DRAFT targeting a backend key
  (content-addressed `get_or_create`: a known hash returns the existing READY
  row, restoring it if trashed). Bytes then arrive from a source and
  `File.finalize(expected_hash=…, expected_size=…)` re-hashes, detects MIME,
  dedups, and flips to READY — source-agnostic, with the expected values
  asserted only when a source supplies them. Today the one source is a client
  upload (`File.issue_upload_token` → one REST route `PUT /storage/upload`,
  one-shot signed token = the CSRF property → `File.receive_bytes`); presigned
  S3/GCS, server-side URL fetch, and adopting bytes already on the backend are
  sibling sources that slot in at `finalize` with no spine change. The byte
  lifecycle (`receive_bytes` / `finalize` / `restore` / `purge` / `_fail`)
  lives on the row; only the factory and the token lookup are on the manager.
- **REBAC stores no storage tuples.** Every relation is field-backed
  (`// rebac:field=` over the FKs, owner = `created_by`) or const-backed
  (`admin`), plus an `operator/role`-style `storage/role` namespace
  (`storage_admin`). Per-drive `editor`/`viewer` (incl. the `auth/user:*`
  wildcard) is the grant surface; no signal tuple sync exists at all.
- **Creates are gated by their owner, not by zed.** A per-row REBAC `create`
  cannot evaluate a not-yet-inserted row, so the create factories
  `File.objects.draft` and `Folder.objects.create_in_drive` check
  `drive.has_access("write")` themselves and insert via per-instance `sudo()`
  while the ambient actor stamps `created_by`; the GraphQL mutations are thin
  dispatchers. Console backend/drive CRUD uses `crud(...,
  permission_classes=[StorageAdminPermission], write_context=…)`.
- **`SqidField` (None-safe `SqidsField`) in `angee.fields`:**
  `from_db_value` passes the `NULL` of nullable-FK joins through — the exact
  query REBAC field-backed arrows run (`parent->read`). `SqidMixin` and every
  model across iam/integrate/storage/notes now declare it; raw `SqidsField`
  is gone from source models.
- **python-magic promoted to the locked stack** for finalize MIME detection;
  it is a hard requirement (needs the system libmagic), so detection is a
  single `magic.from_buffer` call with no signature-sniff fallback.
- **Server bookkeeping never rides client data.** The one-shot proxy-token
  envelope and failure reason live in `File.upload_envelope`
  (`editable=False`, not on the GraphQL type) — `metadata` stays purely
  client-owned, so a file writer can never revive a consumed token.
- **Tree and key-space invariants are database facts:** root folder names are
  unique per drive (NULL-parent constraint arm), real folders require a drive
  (check constraint), folder moves reject cycles in `clean()`, and
  `(backend, prefix)` is unique so two drives never share a key space.
  Dedup hits and finalize races against a *trashed* row restore it instead of
  handing out a purge-doomed row or blocking the re-upload.
- **Audit-reference projections are shared, not copied.** Storage reuses
  `angee.iam.identity.user_public_id` / `user_display_label` (the home the
  knowledge work established) instead of carrying its own. `angee.mixins.actor_user_id`
  is the one reading of "current actor as a user id" (used by `AuditMixin`,
  `File.delete`, the proxy push gate). The layering test derives its addon
  list from `addons/angee/` so new base addons are guarded automatically.
- **The proxy upload view is covered and honest.** A view-level test pins
  token extraction, the streamed PUT, one-shot reuse rejection, and the
  anonymous-actor denial; the docstring states plainly that the token binds
  the PUT (single-use/CSRF) while identity is the request actor (session), not
  the token. `FileQuerySet.delete()` refuses bulk delete so soft-trash can't be
  bypassed, and `for_upload_token` pins the validated nonce so the locked
  consume binds to *that* token.
- Deferred: S3 backend (`boto3` proposed row), drive-sharing UI (grant tuples
  exist), `storage_prune` cron wiring, serving `/media/` in dev, multipart
  upload, per-request batching for `FileType.url`/`created_by_label` on long
  lists, Trash backfill for pre-addon users, the storage web addon (next
  pass).

## 2026-06-05 — Framework core review fixes

A six-reviewer pass over `angee/{base,graphql,compose}` (21 findings), executed
and verified (243 backend tests, mypy + ruff clean, `angee build --check` and
`schema --check` green). Durable decisions now in code:

- **CSRF is real.** The IAM middleware fragment installs `CsrfViewMiddleware`;
  bearer-authenticated requests are marked CSRF-exempt in the actor/auth layer
  (the DRF session-vs-token pattern), so cookie clients are protected and token
  clients keep working.
- **Compose drift is check-only on boot.** `ComposeConfig.import_models()` heals
  the runtime in place (write-only `emit_if_stale()`); the destructive
  reset/prune `emit()` is reachable only through `angee build` / `angee clean`.
  Production ASGI workers no longer pay a per-boot full re-render, and a stale
  runtime yields a clean `CommandError`, not an `ImproperlyConfigured` traceback.
- **Destructive deletes require confirmation.** The GraphQL CRUD `delete`
  resolver defaults `confirm=False`: a bare call returns a non-destructive
  `DeletePreview`; deletion needs `confirm: true`. Preview + delete run inside one
  `transaction.atomic()`.
- **Elevated writes** — `crud(..., write_context="system")` runs the
  create/update/delete body under `system_context` *after* the permission gate,
  so const-admin surfaces (IAM) use the generic CRUD instead of hand-rolled
  mutations.
- Serialization (`json_safe`) maps non-finite floats and normalizes
  `set`/`frozenset`/`bytes` so the subscription/resource wire stays valid,
  deterministic JSON. Public-id lookup is polymorphic on `SqidMixin` (no
  type-switching from outside). The Strawberry schema subclass `AngeeSchema`
  lives in `angee/graphql/schema.py`.
- **Non-findings recorded so they are not re-litigated:** unparenthesized
  `except` tuples are valid on Python 3.14 (the code uses the parenthesized form
  regardless); the select_related-on-a-REBAC-guarded-relation trap does not occur
  in `angee/graphql/` (the one guarded `select_related` runs under
  `system_context`).

## 2026-06-04 — Decomposition & pattern-standardization audit sweep

A whole-tree, addon-by-addon review against the repo's own guidelines (58
findings: 16 high / 22 medium / 20 low) plus the A1–A11 / B1–B4 library- and
pattern-consistency items. Owner-first fixes, one commit per unit; all resolved.
Headline outcomes now in code/docs:

- **Manager/QuerySet canon** documented in `docs/backend/guidelines.md`:
  chainable read scopes live on a `*QuerySet` via `Manager.from_queryset(...)`;
  factories and mutations stay on the manager that owns the write.
- `docs/stack.md` reconciled with reality — phantom locked rows demoted, the
  `pyyaml` row added, aspirational rows moved to "Proposed."
- Scattered functions folded onto their owners across base / iam / resources and
  the frontend (`AccountStatus` rollups onto the enum, `OidcIdentityResolver`,
  `DeletionPreviewNode` factories, one `ListView`, typed `DataView` / `Filter`).
- Dead/forked code removed (the operator's forked SDK client → pluggable auth on
  the single SDK client factory; dead `roles.ts` / `fixtures.ts`).

Commit map (newest→oldest): `1e2cbb1` (A1 SDK auth) · `3838048`
(web/storybook) · `e25dba7` / `429b598` (operator) · `080f76f` (integrate/notes)
· `313fae6` (packages/base) · `c026be8` (iam) · `d618b33` (base deletion) ·
`780324d` (iam AccountStatus) · `2ec6c29` (stack.md / B1–B4).

## 2026-06-04 — Repository layout split (Phase A) & framework recomposition

- **Top-level layout** (`5834a5a`): `src/angee/{base,compose}` →
  `angee/{base,compose}` (framework core) and `src/angee/{iam,resources,
  operator,integrate}` → `addons/angee/*` (base addons), with `packages/` for the
  React layer. The directories now mirror the eventual `django-angee` /
  `django-angee-addons` / `angee-react` split **without changing a single
  import**: `angee` stays a PEP 420 namespace (no `__init__.py` at `angee/` or
  `addons/angee/`), so two source roots merge into one `angee.*` namespace.
- **Django-shaped recomposition**: `angee` is now the model toolkit only
  (the `django.db` analog); the GraphQL runtime moved to its own `angee/graphql/`
  package (schema, CRUD, deletion preview, events, publishing, access); `net.py`
  moved to `addons/angee/integrate`. (`json_safe` stays the single public
  serializer in `angee/serialization.py`, consumed by both graphql and
  resources.) `tests/test_layering.py` enforces base ⊥ {graphql, compose,
  addons}, graphql ⊥ compose, and addons ⊥ compose.

## 2026-06 — Base addon lifts

The base addons reached parity with the team's prototypes and are tested end to
end. Details live in code, the addon `AppConfig` docstrings, and the agent memory
index; summarized here because the working lift plans were retired.

- **IAM** — the swappable `iam.User`, OIDC login (public + console GraphQL),
  third-party **connections** (`Vendor` / `ExternalAccount` / `OAuthClient` /
  `Credential` with a `CredentialKind` handler registry), and a Permission Hub
  console built on **roles-as-namespace** (`rebac.roles`; no `Role` model). The
  superuser ↔ `angee/role:admin` grant fires on `save()`.
- **Integrate** — a thin capability/bridge runtime seam: abstract `Capability` /
  `Bridge`, `WebhookSubscription`, and the `registry` / `scheduler` / `net` /
  `webhooks` / `events` modules. The connection models (vendor/account/client/
  credential) live in IAM, not integrate.
- **Operator** — a thin Django bridge that hands the browser a connection to the
  Go daemon, plus an 8-section console. The daemon mints scoped tokens over a
  server-side admin bearer; the console's GraphQL types are codegen'd from the
  **daemon's** introspected SDL (never hand-written); a `managed=False`
  `OperatorConnection` anchor const-gates the table-less resource to
  `angee/role:admin`.
- **Base / GraphQL** — `EncryptedField` (Fernet at rest), Relay `AngeeNode` +
  `sqid` boundary, and addon-level `AggregateBuilder` wiring.

## Deferred follow-ups

Forward-looking work consciously deferred; kept here so it survives the plans
that proposed it.

- **Generic-list title/status is a backend contract.** `@angee/base` has no owner
  for *which* row field is the "title" vs the "status" of a generic list row; a
  frontend heuristic would violate find-the-owner. The real owner is the GraphQL
  schema: it should annotate title/status fields (a backend contract change),
  then the list view reads the annotation. (Residual of the audit's pkgbase-001.)
- **Machine identity (deferred from the IAM lift).** A full machine-identity
  model (`Service` accounts, hashed `ApiKey`, `ImpersonationEvent` audit) and a
  runtime `auth/role` hierarchy (`includes` / `effective_member`) were planned but
  superseded by roles-as-namespace + the Permission Hub. If revisited: ApiKey
  tokens are **sha256-hashed, not Argon2** (a slow KDF buys nothing for
  high-entropy random tokens), and `last_used_at` is bumped via an unscoped
  `_base_manager.update()`, never `instance.save()` (which trips the write gate
  and `auto_now`). When deriving operator/edge token scope from roles, use
  `effective_member` (the authz boundary, folds in the role hierarchy), never
  `roles_of` / `roleRefs` (a direct-grants UX hint) — the latter under-grants by
  dropping implied roles.
- **Composer extraction seam (strategic).** No packaged tool fills Angee's niche —
  *deterministic build-time emission of generated Django source from
  declaratively-composed addon contracts*. The clean extraction boundary is
  everything above `AngeeRuntime.render_sources() -> dict[Path, str]` (generic
  discover / order / drift / emit / clean / sentinel behind an Emitter protocol,
  with Angee's model / REBAC / history / SDL emitters as the implementations).
  The biggest gap for a public package is entry-point discovery
  (`importlib.metadata`, as pluggy / DJP / stevedore do); today Angee discovers
  only `AppConfig`s already in `INSTALLED_APPS`.
