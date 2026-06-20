import * as React from "react";
import {
  Action,
  Column,
  ControlBandProvider,
  DataPage,
  DrawerDataPage,
  Field,
  Form,
  Group,
  List,
  NEW_RECORD_ID,
  useRecordActionMutation,
} from "@angee/base";
import { useModelInvalidation } from "@angee/sdk";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";

const TEMPLATE_MODEL = "integrate.Template";
const SOURCE_MODEL = "integrate.Source";
const TEMPLATE_SOURCE_KIND = "template";
const TEMPLATE_SOURCE_DEFAULTS = { kind: TEMPLATE_SOURCE_KIND };
const TEMPLATE_KIND_OPTIONS = [
  { value: TEMPLATE_SOURCE_KIND, label: "Template" },
];

const templateList = (
  <List model={TEMPLATE_MODEL} pageSize={50}>
    <Column field="kind" />
    <Column field="name" />
    <Column field="path" />
    <Column field="source.ref" header="Source" />
    <Column field="updatedAt" />
  </List>
);

const templateSourceList = (
  <List model={SOURCE_MODEL} pageSize={50}>
    <Column field="repository.name" header="Repository" />
    <Column field="path" />
    <Column field="ref" />
    <Column field="lastSyncedAt" />
  </List>
);

// Templates are reconciled from template Source rows; this surface manages those
// sources and then inspects the discovered Copier manifest metadata.
export function TemplatesPage(): React.ReactElement {
  const t = useIntegrateT();
  const refreshTemplates = useModelInvalidation(TEMPLATE_MODEL);
  const refreshSources = useModelInvalidation(SOURCE_MODEL);
  const afterSyncTemplates = React.useCallback(
    () => {
      refreshSources();
      refreshTemplates();
    },
    [refreshSources, refreshTemplates],
  );
  const [syncTemplates] = useRecordActionMutation<ActionFieldName>(
    "refreshSource",
    { afterSuccess: afterSyncTemplates },
  );

  return (
    <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-8 px-6 py-6 sm:px-8">
      <Section title={t("integrate.templateSources.title")}>
        <DrawerDataPage
          model={SOURCE_MODEL}
          filter={{ kind: { exact: TEMPLATE_SOURCE_KIND } }}
          createDefaults={TEMPLATE_SOURCE_DEFAULTS}
        >
          {templateSourceList}
          <Form model={SOURCE_MODEL}>
            {/* `kind` is create-only (not read-only) so the template seed is submitted. */}
            <Field name="repository" createOnly />
            <Group label={t("integrate.templateSources.pointer")} columns={2}>
              <Field
                name="kind"
                widget="select"
                options={TEMPLATE_KIND_OPTIONS}
                createOnly
              />
              <Field name="ref" />
            </Group>
            <Field name="path" />
            <Field name="lastSyncedAt" readOnly />
            <Action
              id="syncTemplates"
              label={t("integrate.templateSources.sync")}
              icon="refresh"
              run={syncTemplates}
              visibleWhen={(record) => String(record.id ?? "") !== NEW_RECORD_ID}
            />
          </Form>
        </DrawerDataPage>
      </Section>

      <Section title={t("integrate.templates.title")}>
        <ControlBandProvider host={undefined}>
          <DataPage model={TEMPLATE_MODEL} placement="inline" routed hideCreate>
            {templateList}
            <Form model={TEMPLATE_MODEL}>
              <Field name="name" title readOnly />
              <Group label={t("integrate.templates.template")} columns={2}>
                <Field name="kind" readOnly />
                <Field name="path" readOnly />
              </Group>
              <Field name="source" label={t("integrate.templates.source")} readOnly />
              <Field name="inputs" widget="json" readOnly />
            </Form>
          </DataPage>
        </ControlBandProvider>
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section className="grid gap-3">
      <h2 className="text-15 font-semibold text-fg">{title}</h2>
      {children}
    </section>
  );
}
