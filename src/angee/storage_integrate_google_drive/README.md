# Google Drive mounts

Install `angee.storage_integrate_google_drive` in the project's `INSTALLED_APPS`. It
adds a Google Drive backend to the storage Mounts that `angee.storage_integrate`
owns: open **Files → Mounts** and choose **Connect → Connect Google Drive**.

## Connecting

The connect dialog asks for three things:

1. **The Google account.** Pick one of your connected Google accounts, or
   connect one through the deployment's `google` OAuth client. Credentials are
   encrypted and managed by `angee.integrate`; this addon stores no token.
2. **The folder.** Browse **My Drive** (the whole drive or one folder in it)
   and the **shared drives** the account belongs to. Files shared with you that
   live outside your My Drive are not offered.
3. **How files are handled.**
   - **Leave files in place** (reference mode) makes the Google Drive folder the
     storage of the mount's Angee drive. Files and folders are indexed; bytes
     are read from Google Drive when someone opens a file. The drive is
     read-only: uploads, new folders and deletes are refused.
   - **Copy files** (copy mode) copies the bytes into the default storage, or
     into another writable storage backend chosen in the dialog.

One Google Drive folder is one mount, whoever connects it: a folder some mount
already uses, under its id or an alias such as My Drive's, is offered as already
mounted, even to another user who can read it with their own account. Share the
existing mount's drive instead.

**A reference mount delegates its owner's read access.** Everyone who may read
the mount's Angee drive reads its indexed files through the mount owner's Google
account, whether or not Google Drive shares the file with them. Grant the drive
only to people who may see what the owner sees in that folder. A copy mount is
an ordinary Angee drive of copies, with the same caution.

## Syncing

**Sync now** walks the whole mounted folder. A file whose size and modification
time are unchanged is skipped. Indexing uses the SHA-256 checksum Google Drive
records for each file, so a reference sync downloads nothing; a file without
that checksum is streamed once to hash it. A copy sync downloads only files
whose bytes are not yet stored.

Each file keeps its Angee identity through renames and moves inside the mounted
folder, in either mode. When one folder holds several items of the same name, the earliest keeps
the name and each later one is shown with the start of its file id, for example
`report (1AbCdEfG).pdf`. A file removed from the folder or moved to the Google
Drive trash is moved to the Angee trash by a reference sync; a copy mount keeps
its copies.

Skipped and counted as **skipped** in the sync result:

- Google-native documents (Docs, Sheets, Slides, Forms, Drawings and every other
  `application/vnd.google-apps.*` type except folders), which have no bytes to
  mirror;
- shortcuts;
- in copy mode, files Google Drive does not let the account download.

A file Google refuses to serve, or whose download breaks, is counted as an error
and the rest of the sync goes on. A rate limit or a Google outage fails the whole
sync as retryable, with Google's `Retry-After` when it sends one. Nothing
reschedules a failed sync of a mount that does not poll yet: run **Sync now**
again later. A polling mount (`poll_enabled` in its config) retries at its next
poll, no earlier than Google asked. When Google no longer accepts the account,
or the account has not granted Drive access, the sync fails and the account asks
to be reconnected.

Opening a file of a reference mount reads it from Google Drive with the mount's
own account, while the mount is connected. A file moved to the Google Drive
trash, or changed since the last sync, is refused until the next sync.

## Scope

The backend reads with the `https://www.googleapis.com/auth/drive.readonly`
scope (`GoogleDriveMountBackend.oauth_scopes`); only connected accounts that
granted it are offered. Google classifies `drive.readonly` as a **restricted** scope: an
app that requests it from users outside its own Google Workspace organization
needs Google's OAuth verification, including a security assessment. An
internal app (a Workspace organization's own users only) does not.

## Checking it against Google

Set up Google and the deployment's `google` client as
[Google Calendar's check](../calendar_integrate_google/README.md#set-up) does:
the Google Drive API enabled, `drive.readonly` among its Data Access scopes,
and the redirect URI `<console origin>/integrate/oauth/callback`. Then:

1. **Files → Mounts** (`/storage/mounts`) → **Connect → Connect Google Drive**.
   Choose an account, or **Connect another account**: Google asks for read-only
   Drive access and returns to Mounts. An account connected only for another
   Google feature is offered once it grants Drive this way, and keeps what it
   granted before.
2. Under **Source folder**, browse My Drive and the shared drives, choose a
   folder and **Use this folder**. With **Leave files in place**, **Connect**: the
   first sync is queued.
3. Browse the mount's drive in **Files** and open a file: its bytes are read
   from Google Drive. Google-native documents and shortcuts are counted as
   skipped.
4. Connect another folder with **Copy files** into **Default storage**: its
   files' bytes are copied there, and opening one reads the copy.

The mount's **Sync** fields and the worker logs show what each sync did;
[Syncing](#syncing) is what to expect of a later one.
