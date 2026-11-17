# Register the deployment's Google client

One Google OAuth client per deployment serves every Google feature: sign-in, mail,
calendar, drive, video and Gemini through the Cloud APIs. The `integrate_google`
addon seeds it as the `google` OAuth client with the Google endpoints, claims and
scope catalogue, and leaves its client id and secret blank. Google forbids
publishing client credentials, so each deployment registers its own client in a
Google Cloud project it controls and pastes the two values into that row.

Each feature asks for its own scopes when a user connects an account; the client's
default scopes stay `openid email profile`. Google's incremental authorization
keeps the scopes an account granted before, so connecting calendar after mail
keeps both. One user can connect several Google accounts through the client;
Google shows its account chooser on every connect.

## Create the client

1. In the [Google Cloud console](https://console.cloud.google.com/), select or
   create the project that will own the client. Its API quota is shared by every
   account the deployment connects.
2. Enable the APIs the deployment's features use, for example Gmail, Google
   Calendar, Google Drive or YouTube Data API v3.
3. Configure the OAuth consent screen (Google Auth Platform, **Branding** and
   **Audience**):
   - **Internal** when only accounts of your Google Workspace organisation
     connect. Internal clients need no Google verification and have no user cap.
   - **External** when personal Google accounts connect. Set the publishing status
     to **In production**. A client left in **Testing** issues refresh tokens that
     expire after 7 days, so every connected account needs reconnecting weekly.
     An unverified client in production shows users an "unverified app" warning
     and is limited to 100 users.
4. Add the scopes the deployment uses under **Data Access**. Google classes them:
   - non-sensitive: `openid`, `email`, `profile`;
   - sensitive: the calendar, `drive.file` and YouTube scopes;
   - restricted: `https://mail.google.com/` and `drive.readonly`. A public,
     verified client requesting a restricted scope needs Google's security
     assessment, repeated every year. Internal clients, and unverified external
     clients within the 100-user limit, do not.
5. Create an OAuth client id of type **Web application** under **Clients**. Add
   both redirect URIs, with the deployment's public origin:
   - `https://<console-host>/integrate/oauth/callback` for connecting accounts;
   - `https://<console-host>/sso/callback` for signing in with Google.

   Google accepts HTTPS redirects on a public domain, and `http://localhost` for a
   local stack. LAN addresses and `.local` hosts are refused.
6. Copy the client secret when Google shows it: it is displayed only once.

## Configure the deployment

Paste the client id and secret into the `google` OAuth client in the console
(**Integrations > OAuth > OAuth providers**). The row reads `needs_client` until both are
set, and `ready` afterwards. A host that keeps secrets in its environment declares
the client in `ANGEE_INTEGRATE_OAUTH_CLIENTS` instead and syncs it with
`manage.py oauth_clients`.

With the OIDC login addon installed, a new deployment's `google` client also
signs users in: its login and email-match switches start on, so an existing user
signs in with the Google account whose verified email matches theirs. A `google`
client that existed before is never changed; turn those switches on yourself.
Restrict sign-in to your domains with the client's allowed email domains.

## Keep the client alive

- Google deletes an OAuth client that has not been used for 6 months. A deleted
  client cannot be restored; register a new one and reconnect every account.
- A refresh token stops working when the user revokes access, changes the
  password of an account that granted mail access, or has not used it for 6
  months. The connection then asks the user to reconnect.
