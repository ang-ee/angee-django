import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { Action, Button, Column, ResourceList, Field, Form, Glyph, Group, List, MutationDialog, useRecordActionMutation, type MutationDialogField } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

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
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_integration");
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate toolbarActions={<ConnectCardDav />}>
      <List resource={MODEL}>
        <Column field="display_name" header={t("directory.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="backend_class" />
        <Column field="sync_stage" />
        <Column field="last_sync_status" />
        <Column field="last_sync_items" />
        <Column field="last_sync_completed_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="display_name" title readOnly />
        <Field name="lifecycle" readOnly />
        <Field name="runtime_status" readOnly />
        <Field name="backend_class" readOnly />
        <Field name="config" readOnly />
        <Group label={t("directory.group.lastSync")} columns={2}>
          <Field name="is_syncing" readOnly />
          <Field name="sync_stage" readOnly />
          <Field name="sync_error" readOnly />
          <Field name="sync_progress" widget="json" readOnly />
          <Field name="last_sync_summary" widget="json" readOnly />
          <Field name="last_sync_status" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Action id="sync" label={t("directory.action.sync")} icon="refresh" run={sync} />
      </Form>
    </ResourceList>
  );
}

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
      cancelLabel={t("directory.connect.cancel")}
      errorFallback={t("directory.connect.error")}
      onSubmit={(values) =>
        connect({
          name: stringValue(values.name),
          serverUrl: stringValue(values.serverUrl),
          username: stringValue(values.username),
          password: stringValue(values.password),
        })
      }
    />
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
