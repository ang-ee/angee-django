import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { Action, Column, Field, Form, Group, List, ResourceList, useRecordActionMutation } from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL, VERSION_MODEL } from "../resources";

/**
 * The versions of every form: the questions as a checked SurveyJS definition, the
 * languages they are written in and which of them hold personal data. A version
 * is revised until it is published; after that it is read only, and a change is a
 * new version.
 */
export function VersionsPage(): React.ReactElement {
  const t = useWebformsT();
  const [publish] = useRecordActionMutation<ActionFieldName>("publish_webform_version", {
    idArgument: "version", invalidateModels: [VERSION_MODEL, FORM_MODEL],
  });
  return (
    <ResourceList resource={VERSION_MODEL} placement="inline" routed>
      <List resource={VERSION_MODEL} order={{ created_at: "DESC" }}>
        <Column field="form" />
        <Column field="number" />
        <Column field="default_language" />
        <Column field="published_at" />
        <Column field="created_at" />
      </List>
      <Form resource={VERSION_MODEL} readOnlyWhen={(row) => Boolean(row.published_at)}>
        <Group columns={2}>
          <Field name="form" createOnly />
          <Field name="number" readOnly showWhen={(row) => Boolean(row.number)} />
          <Field name="languages" widget="tagInput" />
          <Field name="default_language" />
          <Field name="contact_question" label={t("version.contact")} />
          <Field name="published_at" readOnly showWhen={(row) => Boolean(row.published_at)} />
        </Group>
        <Field name="personal_questions" label={t("version.personal")} widget="json"
          description={t("version.personalHint")} />
        <Field name="definition" widget="json" />
        <Action id="publish" label={t("version.publish")} primary
          confirm={{ title: t("version.publish"), body: t("version.publishConfirm") }} run={publish} />
      </Form>
    </ResourceList>
  );
}
