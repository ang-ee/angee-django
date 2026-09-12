import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { Button, Column, ResourceList, Field, Form, Glyph, List, MutationDialog, mutationDialogValueCodecs, registerForm, type MutationDialogField, type MutationDialogValues, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncColumns, IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { ConnectCardDavDirectory } from "./documents";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Directory";

/**
 * Connected contacts directories. The "Connect CardDAV" control opens a connect
 * dialog (one mutation creates the credential + directory); rows are model-driven
 * via ResourceList, each detail carrying a declarative "Sync now" record action.
 * Directories are created through the connect flow and have no delete root, so the
 * form is read-only (`hideCreate`) and no delete affordance renders — a directory
 * is removed by deleting the integration, and its synced contacts by the source.
 */
export function DirectoriesPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={MODEL} form={directoryForm} placement="inline" routed hideCreate toolbarActions={<ConnectCardDav />}>
      <List resource={MODEL}>
        <Column field="display_name" header={t("directory.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        {IntegrationSyncColumns()}
      </List>
    </ResourceList>
  );
}

function DirectoryForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const syncAction = useIntegrationSyncAction("sync_integration", t("directory.action.sync"));
  return (
      <Form {...props} resource={MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="lifecycle" readOnly />
        <Field name="runtime_status" widget="colorDot" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        {IntegrationSyncFields({ label: t("directory.group.lastSync") })}
        {syncAction}
      </Form>
  );
}

export const directoryForm = registerForm(MODEL, DirectoryForm);

/** Button + dialog that connects a CardDAV account, for the list toolbar slot. */
function ConnectCardDav(): React.ReactElement {
  const t = usePartiesT();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("directory.connect.button")}
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
  // Correct as-is: parties.Directory declares changes(Directory, field="directoryChanged").
  const [connect] = useAuthoredMutation(ConnectCardDavDirectory, {
    invalidateModels: [MODEL],
  });
  const t = usePartiesT();
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("directory.connect.name"),
        placeholder: t("directory.connect.namePlaceholder"),
        required: true,
      },
      {
        name: "serverUrl",
        label: t("directory.connect.serverUrl"),
        placeholder: t("directory.connect.serverUrlPlaceholder"),
        required: true,
      },
      {
        name: "username",
        label: t("directory.connect.username"),
        required: true,
      },
      {
        name: "password",
        label: t("directory.connect.password"),
        widget: "password",
        required: true,
      },
    ],
    [t],
  );

  return (
    <MutationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("directory.connect.title")}
      description={t("directory.connect.description")}
      fields={fields}
      submitLabel={t("directory.connect.submit")}
      submittingLabel={t("directory.connect.submitting")}
      errorFallback={t("directory.connect.error")}
      parseValues={parseDirectoryValues}
      onSubmit={connect}
    />
  );
}

function parseDirectoryValues(values: MutationDialogValues) {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    serverUrl: mutationDialogValueCodecs.requiredString(
      values.serverUrl,
      "serverUrl",
    ),
    username: mutationDialogValueCodecs.requiredString(values.username, "username"),
    password: mutationDialogValueCodecs.verbatimString(values.password, "password"),
  };
}
