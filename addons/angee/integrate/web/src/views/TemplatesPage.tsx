import * as React from "react";
import { Action, Column, ControlBandProvider, ResourceList, DrawerResourceList, Facet, Field, Form, Group, List, REFINE_CREATE_ID, SettingsSection, SettingsShell, useRecordActionMutation } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";

const TEMPLATE_MODEL = "integrate.Template";
const SOURCE_MODEL = "integrate.Source";
const TEMPLATE_SOURCE_KIND = "template";
const TEMPLATE_SOURCE_DEFAULTS = { kind: TEMPLATE_SOURCE_KIND };
const TEMPLATE_KIND_OPTIONS = [
  { value: TEMPLATE_SOURCE_KIND, label: "Template" },
];

function templateList(t: ReturnType<typeof useIntegrateT>): React.ReactElement {
  return (
    <List resource={TEMPLATE_MODEL} pageSize={50}>
      <Facet field="source" label={t("col.source")} labelField="path" />
      <Column field="kind" />
      <Column field="name" />
      <Column field="path" />
      <Column field="source.ref" header={t("col.source")} />
      <Column field="updated_at" />
    </List>
  );
}

function templateSourceList(
  t: ReturnType<typeof useIntegrateT>,
): React.ReactElement {
  return (
    <List resource={SOURCE_MODEL} pageSize={50}>
      <Facet field="repository" label={t("col.repository")} labelField="name" />
      <Column field="repository.name" header={t("col.repository")} />
      <Column field="path" />
      <Column field="ref" />
      <Column field="last_synced_at" />
    </List>
  );
}

// Templates are reconciled from template Source rows; this surface manages those
// sources and then inspects the discovered Copier manifest metadata.
export function TemplatesPage(): React.ReactElement {
  const t = useIntegrateT();
  const [syncTemplates] = useRecordActionMutation<ActionFieldName>(
    "refresh_source",
    { invalidateModels: [SOURCE_MODEL, TEMPLATE_MODEL] },
  );

  return (
    <SettingsShell maxWidth="1200" gap="8">
      <SettingsSection title={t("templateSources.title")}>
        <DrawerResourceList
          resource={SOURCE_MODEL}
          baseFilter={{ kind: { exact: TEMPLATE_SOURCE_KIND } }}
          createDefaults={TEMPLATE_SOURCE_DEFAULTS}
        >
          {templateSourceList(t)}
          <Form resource={SOURCE_MODEL}>
            {/* `kind` is create-only (not read-only) so the template seed is submitted. */}
            <Field name="repository" createOnly />
            <Group label={t("templateSources.pointer")} columns={2}>
              <Field
                name="kind"
                widget="select"
                options={TEMPLATE_KIND_OPTIONS}
                createOnly
              />
              <Field name="ref" />
            </Group>
            <Field name="path" />
            <Field name="last_synced_at" readOnly />
            <Action
              id="syncTemplates"
              label={t("templateSources.sync")}
              icon="refresh"
              run={syncTemplates}
              visibleWhen={(record) => String(record.id ?? "") !== REFINE_CREATE_ID}
            />
          </Form>
        </DrawerResourceList>
      </SettingsSection>

      <SettingsSection title={t("templates.title")}>
        <ControlBandProvider host={undefined}>
          <ResourceList resource={TEMPLATE_MODEL} placement="inline" routed hideCreate>
            {templateList(t)}
            <Form resource={TEMPLATE_MODEL}>
              <Field name="name" title readOnly />
              <Group label={t("templates.template")} columns={2}>
                <Field name="kind" readOnly />
                <Field name="path" readOnly />
              </Group>
              <Field name="source" label={t("templates.source")} readOnly />
              <Field name="inputs" widget="json" readOnly />
            </Form>
          </ResourceList>
        </ControlBandProvider>
      </SettingsSection>
    </SettingsShell>
  );
}
