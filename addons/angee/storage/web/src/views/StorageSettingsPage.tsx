import { type ReactElement, type ReactNode } from "react";

import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  List,
} from "@angee/base";
import { useStorageT } from "../i18n";

const DRIVE_MODEL = "storage.Drive";
const BACKEND_MODEL = "storage.Backend";

/**
 * The storage admin console: managed lists for drives and backends, each a
 * `ResourceList` whose record form opens in a drawer. Both surfaces are
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
        <DrawerResourceList resource={DRIVE_MODEL} hideCreate>
          <List resource={DRIVE_MODEL} order={{ name: "ASC" }}>
            <Column field="name" />
            <Column field="slug" />
            <Column field="prefix" />
            <Column field="is_archived" widget="booleanBadge" />
          </List>
          <Form resource={DRIVE_MODEL}>
            <Field name="name" widget="text" title />
            <Field name="slug" createOnly />
            <Field name="prefix" />
            <Field name="description" widget="textarea" />
            <Field name="is_archived" label={t("storage.settings.archived")} widget="switch" />
          </Form>
        </DrawerResourceList>
      </Section>

      <Section
        title={t("storage.settings.backends.title")}
        description={t("storage.settings.backends.description")}
      >
        <DrawerResourceList resource={BACKEND_MODEL}>
          <List resource={BACKEND_MODEL} order={{ label: "ASC" }}>
            <Column field="label" />
            <Column field="slug" />
            <Column field="backend_class" />
            <Column field="is_default" widget="booleanBadge" />
            <Column field="is_archived" widget="booleanBadge" />
          </List>
          <Form resource={BACKEND_MODEL}>
            <Field name="label" widget="text" title />
            <Field name="slug" createOnly />
            <Field name="backend_class" label={t("storage.settings.backendClass")} />
            <Field name="backend_config" label={t("storage.settings.config")} widget="json" />
            <Field name="is_default" label={t("storage.settings.default")} widget="switch" />
            <Field name="is_archived" label={t("storage.settings.archived")} widget="switch" editOnly />
          </Form>
        </DrawerResourceList>
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
