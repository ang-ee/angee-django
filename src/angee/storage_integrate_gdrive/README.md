# Google Drive mounts

Install `angee.storage_integrate_gdrive` in the project's `INSTALLED_APPS`. It
adds a Google Drive backend to the storage Mounts that `angee.storage_integrate`
owns: open **Storage → Mounts** and choose **Connect Google Drive**.

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

One Google Drive folder is one mount: a folder some mount already uses is
offered as already mounted.

## Syncing

**Sync now** walks the whole mounted folder. A file whose size and modification
time are unchanged is skipped. Indexing uses the SHA-256 checksum Google Drive
records for each file, so a reference sync downloads nothing; a file without
that checksum is streamed once to hash it. A copy sync downloads only files
whose bytes are not yet stored.

Each file keeps its Angee identity through renames and moves inside the mounted
folder. When one folder holds several items of the same name, the earliest keeps
the name and each later one is shown with the start of its file id, for example
`report (1AbCdEfG).pdf`. A file removed from the folder or moved to the Google
Drive trash is moved to the Angee trash by a reference sync; a copy mount keeps
its copies.

Skipped and counted as **skipped** in the sync result:

- Google-native documents (Docs, Sheets, Slides, Forms, Drawings and every other
  `application/vnd.google-apps.*` type except folders), which have no bytes to
  mirror;
- shortcuts.

A rate limit or a Google outage fails the sync as retryable; the worker retries
it. When Google no longer accepts the account, the sync fails and the account
asks to be reconnected.

## Scope

The backend reads with the `https://www.googleapis.com/auth/drive.readonly`
scope (`GoogleDriveMountBackend.oauth_scopes`); the connected account must have
granted it. Google classifies `drive.readonly` as a **restricted** scope: an
app that requests it from users outside its own Google Workspace organization
needs Google's OAuth verification, including a security assessment. An
internal app (a Workspace organization's own users only) does not.
