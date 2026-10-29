# Notion connections

Install `angee.knowledge_integrate_notion` in the project's `INSTALLED_APPS`.
Open **Integrations → Notion**, create a connection, and create or select an
**API token** credential in its credential picker. Credentials are encrypted and
managed by `angee.integrate`; the Notion addon stores no token of its own.

Save the connection and use the inherited **Resume** and **Test connection**
actions. Credential attachment and Resume validate the token through Notion's
[`GET /v1/users/me`](https://developers.notion.com/reference/get-self) endpoint
using the integration owner's discovery lifecycle and HTTP transport. Give an
internal Notion connection read-content capability and access to the pages that
will be imported.

Use **Sync now** to import the pages shared with that token into the connection's
private Knowledge vault. Connected, discovered bridges also use Integrate's
normal polling schedule. The form exposes the destination vault, shared sync
status, and the Streams panel for record-level issues and retries.

The bridge combines Notion search with data-source queries, imports database rows
as pages and data sources as folders, and attaches hierarchy after the identities
exist. Knowledge owns page creation, permissions, body history, hashing and
backlinks. Integrate owns resumable cursors, source identity, retries and conflict
resolution; rerunning the sync updates the same pages.

**Rewrite page links** is enabled by default. Links and page mentions whose
targets have been imported into this connection's vault use Knowledge's native
wikilinks, including backlinks. The final identity pass resolves forward links;
later imports and title changes refresh their destinations. Turn the option off
and sync to keep Notion destinations instead. Unimported targets, self references,
and block anchors retain their original URL. Original exports remain on the
source observation, and remapping follows the same local-edit conflict policy.

Content comes from Notion's native
[Markdown export](https://developers.notion.com/reference/retrieve-page-markdown).
The addon translates Notion's enhanced tags into ordinary Markdown for the shared
Knowledge renderer: references and media become links, toggles and columns become
readable sections, and tables become pipe tables. Colors and layout are omitted;
the original export is retained on the source observation. Code remains literal.
Embed placeholders in otherwise complete exports become labeled links back to
the Notion block; the bridge does not claim to reproduce the embedded content.
Incomplete exports and inaccessible pages are quarantined without replacing an
existing body. Database properties remain in the retained source observation;
this bridge does not turn them into Knowledge fields or copy remote media files.

This is a pull-only connection. Local edits become explicit conflicts; **Keep
remote** applies a fresh Notion read through the shared conflict action. No
changes are sent to Notion. Imported pages are retained if their source is
trashed or disappears from search, since Notion's
[search inventory is not exhaustive](https://developers.notion.com/reference/search-optimizations-and-limitations).
The connection's owner must remain active and able to write its destination
vault; changing owners never transfers access to the imported knowledge.

Workspace users and Notion sharing permissions are not imported. Notion's
[user API](https://developers.notion.com/reference/get-users) exposes profiles
when the connection has user-information capability; its listing omits guests
and is unavailable to personal access tokens. Email access requires the
corresponding [capability](https://developers.notion.com/reference/capabilities).
The documented [page API](https://developers.notion.com/reference/page) does not
provide a page-sharing ACL to mirror. Access to imported pages follows the
private vault's native Knowledge permissions; source access does not grant
other Angee users access. Retained source observations preserve any author IDs
returned by Notion without provisioning local accounts or inventing grants.
