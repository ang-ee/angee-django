import * as React from "react";
import {
  Button,
  Dialog,
  EmptyState,
  FieldLabel,
  FieldRoot,
  Glyph,
  Input,
  TextLink,
} from "@angee/base";
import {
  errorMessage,
  useActionMutation,
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/sdk";

import {
  ConnectCardDavDirectory,
  PartiesDirectories,
  type DirectoryRow,
} from "./documents.console";

const DIRECTORY_VARS = { pagination: { offset: 0, limit: 50 } };

/** Directories: connect a CardDAV address book and sync its contacts into People. */
export function DirectoriesPage(): React.ReactElement {
  const [connecting, setConnecting] = React.useState(false);
  const query = useAuthoredQuery(PartiesDirectories, DIRECTORY_VARS);
  const directories = query.data?.directories.results ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-15 font-semibold text-fg">Directories</h1>
          <p className="text-13 text-fg-muted">
            Connect a CardDAV address book to sync its contacts into{" "}
            <TextLink href="/parties/people">People</TextLink>.
          </p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setConnecting(true)}>
          <Glyph decorative name="plus" />
          Connect CardDAV
        </Button>
      </div>

      {directories.length === 0 ? (
        <EmptyState
          icon="address-book"
          title="No directories connected"
          description="Connect a CardDAV account to pull its contacts into People and their Handles."
          actions={
            <Button variant="primary" size="sm" onClick={() => setConnecting(true)}>
              <Glyph decorative name="plus" />
              Connect CardDAV
            </Button>
          }
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {directories.map((directory) => (
            <DirectoryCard key={directory.id} directory={directory} onChanged={query.refetch} />
          ))}
        </ul>
      )}

      <ConnectDialog
        open={connecting}
        onOpenChange={setConnecting}
        onConnected={() => {
          setConnecting(false);
          query.refetch();
        }}
      />
    </div>
  );
}

function DirectoryCard({
  directory,
  onChanged,
}: {
  directory: DirectoryRow;
  onChanged: () => void;
}): React.ReactElement {
  const [sync, syncState] = useActionMutation("syncIntegration");
  const [message, setMessage] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const config = (directory.config ?? {}) as { carddav_url?: string; display_name?: string };

  const runSync = React.useCallback(async () => {
    setError(null);
    setMessage(null);
    try {
      const result = await sync(directory.id);
      setMessage(result ?? "Synced.");
      onChanged();
    } catch (cause) {
      setError(errorMessage(cause, "Sync failed."));
    }
  }, [directory.id, onChanged, sync]);

  return (
    <li className="flex items-center gap-3 rounded-md border border-border bg-sheet px-3 py-2">
      <Glyph decorative name="address-book" className="text-fg-muted" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-13 text-fg">
          {config.display_name || "CardDAV directory"}
        </div>
        <div className="truncate text-12 text-fg-muted">
          {config.carddav_url ?? ""} · {directory.status}
          {directory.lastSyncStatus
            ? ` · last sync ${directory.lastSyncStatus} (${directory.lastSyncItems})`
            : ""}
        </div>
        {error ? (
          <div className="text-12 text-danger-text" role="alert">
            {error}
          </div>
        ) : null}
        {message ? <div className="text-12 text-success-text">{message}</div> : null}
      </div>
      <Button size="sm" disabled={syncState.fetching} onClick={runSync}>
        {syncState.fetching ? "Syncing…" : "Sync now"}
      </Button>
    </li>
  );
}

function ConnectDialog({
  open,
  onOpenChange,
  onConnected,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConnected: () => void;
}): React.ReactElement {
  const [connect, connectState] = useAuthoredMutation(ConnectCardDavDirectory);
  const [name, setName] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setUrl("");
      setUsername("");
      setPassword("");
      setError(null);
    }
  }, [open]);

  const submit = React.useCallback(async () => {
    setError(null);
    try {
      await connect({ name, url, username, password });
      onConnected();
    } catch (cause) {
      setError(errorMessage(cause, "Could not connect the directory."));
    }
  }, [connect, name, url, username, password, onConnected]);

  const ready = url.trim() !== "" && username.trim() !== "" && password !== "";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>Connect a CardDAV directory</Dialog.Title>
                <Dialog.Description>
                  Enter your CardDAV address-book URL and Basic-auth credentials. Its contacts sync into People.
                </Dialog.Description>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            <div className="flex flex-col gap-3">
              <FieldRoot>
                <FieldLabel htmlFor="cd-name">Name</FieldLabel>
                <Input
                  id="cd-name"
                  value={name}
                  placeholder="Personal contacts"
                  onChange={(event) => setName(event.currentTarget.value)}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="cd-url">Address-book URL</FieldLabel>
                <Input
                  id="cd-url"
                  value={url}
                  placeholder="https://dav.example.com/addressbooks/me/contacts/"
                  onChange={(event) => setUrl(event.currentTarget.value)}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="cd-user">Username</FieldLabel>
                <Input
                  id="cd-user"
                  value={username}
                  onChange={(event) => setUsername(event.currentTarget.value)}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="cd-pass">Password</FieldLabel>
                <Input
                  id="cd-pass"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.currentTarget.value)}
                />
              </FieldRoot>
              {error ? (
                <p className="text-13 text-danger-text" role="alert">
                  {error}
                </p>
              ) : null}
            </div>
          </Dialog.Body>
          <Dialog.Footer>
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!ready || connectState.fetching}
              onClick={submit}
            >
              {connectState.fetching ? "Connecting…" : "Connect"}
            </Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
