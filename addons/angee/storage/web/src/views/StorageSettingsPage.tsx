import { useState, type ReactElement, type ReactNode } from "react";

import {
  Column,
  ControlBandProvider,
  DataPage,
  Field,
  Form,
  List,
  NEW_RECORD_ID,
} from "@angee/base";
import { useStorageT } from "../i18n";

const DRIVE_MODEL = "storage.Drive";
const BACKEND_MODEL = "storage.Backend";

/**
 * The storage admin console: managed lists for drives and backends, each a
 * `DataPage` whose record form opens in a drawer. Both surfaces are
 * storage-admin gated server-side (the `backends` query and the drive/backend
 * mutations). Drives are edit-only here — creating one (which binds a backend)
 * lives in the file browser's drive switcher; backends are full CRUD.
 */
export function StorageSettingsPage(): ReactElement {
  const t = useStorageT();
  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-10 px-6 py-6 sm:px-8">
      <Section
        title={t("storage.settings.drives.title")}
        description={t("storage.settings.drives.description")}
      >
        <AdminTable model={DRIVE_MODEL} hideCreate>
          <List model={DRIVE_MODEL} order={{ name: "ASC" }}>
            <Column field="name" />
            <Column field="slug" />
            <Column field="prefix" />
            <Column field="isArchived" widget="booleanBadge" />
          </List>
          <Form model={DRIVE_MODEL}>
            <Field name="name" widget="text" title />
            <Field name="slug" createOnly />
            <Field name="prefix" />
            <Field name="description" widget="textarea" />
            <Field name="isArchived" label={t("storage.settings.archived")} widget="switch" />
          </Form>
        </AdminTable>
      </Section>

      <Section
        title={t("storage.settings.backends.title")}
        description={t("storage.settings.backends.description")}
      >
        <AdminTable model={BACKEND_MODEL}>
          <List model={BACKEND_MODEL} order={{ label: "ASC" }}>
            <Column field="label" />
            <Column field="slug" />
            <Column field="backendClass" />
            <Column field="isDefault" widget="booleanBadge" />
            <Column field="isArchived" widget="booleanBadge" />
          </List>
          <Form model={BACKEND_MODEL}>
            <Field name="label" widget="text" title />
            <Field name="slug" createOnly />
            <Field name="backendClass" label={t("storage.settings.backendClass")} />
            <Field name="backendConfig" label={t("storage.settings.config")} widget="json" />
            <Field name="isDefault" label={t("storage.settings.default")} widget="switch" />
            <Field name="isArchived" label={t("storage.settings.archived")} widget="switch" editOnly />
          </Form>
        </AdminTable>
      </Section>
    </div>
  );
}

/** One titled admin section wrapping a managed table. */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="grid gap-3">
      <header className="grid gap-0.5">
        <h2 className="text-15 font-semibold text-fg">{title}</h2>
        <p className="text-13 text-fg-muted">{description}</p>
      </header>
      {children}
    </section>
  );
}

/** A `DataPage` whose record form opens in a drawer, with self-owned open state. */
function AdminTable({
  model,
  hideCreate,
  children,
}: {
  model: string;
  hideCreate?: boolean;
  children: ReactNode;
}): ReactElement {
  const [recordId, setRecordId] = useState<string | undefined>(undefined);
  // Two managed lists share one page, so each renders its own toolbar inline
  // rather than portaling into the shell's single control band.
  return (
    <ControlBandProvider host={undefined}>
      <DataPage
        model={model}
        placement="drawer"
        hideCreate={hideCreate}
        recordId={recordId}
        onSelect={(id) => setRecordId(id ?? NEW_RECORD_ID)}
        onClose={() => setRecordId(undefined)}
      >
        {children}
      </DataPage>
    </ControlBandProvider>
  );
}
