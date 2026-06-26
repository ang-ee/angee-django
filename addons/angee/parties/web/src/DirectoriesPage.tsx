import * as React from "react";
import {
  Action,
  Button,
  Column,
  ResourceList,
  Dialog,
  Field,
  FieldLabel,
  FieldRoot,
  Form,
  Glyph,
  Group,
  Input,
  List,
  errorMessage,
  useRecordActionMutation,
  useAuthoredMutation,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { ConnectCardDavDirectory } from "./documents.console";

const MODEL = "parties.Directory";

const directoryList = (
  <List resource={MODEL}>
    <Column field="display_name" header="Name" />
    <Column field="status" widget="statusBadge" />
    <Column field="backend_class" />
    <Column field="last_sync_status" />
    <Column field="last_sync_items" />
    <Column field="last_sync_completed_at" />
  </List>
);

/**
 * Connected contacts directories. The "Connect CardDAV" control opens a connect
 * dialog (one mutation creates the credential + directory); rows are model-driven
 * via ResourceList, each detail carrying a declarative "Sync now" record action.
 * Directories are created through the connect flow and have no delete root, so the
 * form is read-only (`hideCreate`) and no delete affordance renders — a directory
 * is removed by deleting the integration, and its synced contacts by the source.
 */
export function DirectoriesPage(): React.ReactElement {
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_integration");
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate toolbarActions={<ConnectCardDav />}>
      {directoryList}
      <Form resource={MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="status" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        <Group label="Last sync" columns={2}>
          <Field name="last_sync_status" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Action id="sync" label="Sync now" icon="refresh" run={sync} />
      </Form>
    </ResourceList>
  );
}

/** Button + dialog that connects a CardDAV account, for the list toolbar slot. */
function ConnectCardDav(): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        Connect CardDAV
      </Button>
      <ConnectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

function ConnectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const [connect, { fetching }] = useAuthoredMutation(ConnectCardDavDirectory, {
    invalidateModels: [MODEL],
  });
  const [name, setName] = React.useState("");
  const [serverUrl, setServerUrl] = React.useState("");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setServerUrl("");
      setUsername("");
      setPassword("");
      setError(null);
    }
  }, [open]);

  const submit = React.useCallback(async () => {
    setError(null);
    try {
      await connect({ name, serverUrl, username, password });
      onOpenChange(false);
    } catch (cause) {
      setError(errorMessage(cause, "Could not connect the directory."));
    }
  }, [connect, name, serverUrl, username, password, onOpenChange]);

  const ready = serverUrl.trim() !== "" && username.trim() !== "" && password !== "";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>Connect a CardDAV account</Dialog.Title>
                <Dialog.Description>
                  Enter your CardDAV server URL and Basic-auth credentials. Each address book
                  syncs into a folder of contacts.
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
                <FieldLabel htmlFor="cd-url">Server URL</FieldLabel>
                <Input
                  id="cd-url"
                  value={serverUrl}
                  placeholder="https://dav.example.com/"
                  onChange={(event) => setServerUrl(event.currentTarget.value)}
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
            <Button variant="primary" size="sm" disabled={!ready || fetching} onClick={submit}>
              {fetching ? "Connecting…" : "Connect"}
            </Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
