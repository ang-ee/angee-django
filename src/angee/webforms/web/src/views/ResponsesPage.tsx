import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Column, Field, Form, Group, List, ResourceList, optionToken, useRecordActionMutation,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { RESPONSE_MODEL } from "../resources";

/** A response its form's target has not taken yet: it can be delivered again or given up on. */
const undelivered = (row: Record<string, unknown>) => ["pending", "failed"].includes(optionToken(row.delivery));

/**
 * The responses forms have kept, read only: each is evidence, as received. What
 * became of one changes only through its owner's verbs: hand it to the form's
 * target again, give up on it, or blank the answers its version marks as personal.
 */
export function ResponsesPage(): React.ReactElement {
  const t = useWebformsT();
  const [deliver] = useRecordActionMutation<ActionFieldName>("deliver_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL],
  });
  const [dismiss] = useRecordActionMutation<ActionFieldName>("dismiss_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL],
  });
  const [erase] = useRecordActionMutation<ActionFieldName>("erase_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL],
  });
  return (
    <ResourceList resource={RESPONSE_MODEL} hideCreate placement="inline" routed>
      <List resource={RESPONSE_MODEL} order={{ created_at: "DESC" }} emptyContent={t("response.empty")}>
        <Column field="form" />
        <Column field="version" />
        <Column field="language" />
        <Column field="delivery" widget="statusBadge" />
        <Column field="delivery_error" />
        <Column field="created_at" />
      </List>
      <Form resource={RESPONSE_MODEL} readOnly>
        <Field name="delivery" widget="statusbar" status />
        <Group columns={2}>
          <Field name="form" />
          <Field name="version" />
          <Field name="language" />
          <Field name="created_at" />
          <Field name="delivered_at" showWhen={(row) => Boolean(row.delivered_at)} />
          <Field name="erased_at" showWhen={(row) => Boolean(row.erased_at)} />
        </Group>
        <Field name="delivery_error" widget="textarea" showWhen={(row) => Boolean(row.delivery_error)} />
        <Field name="answers" widget="json" />
        <Action id="deliver" label={t("response.deliver")} primary visibleWhen={undelivered} run={deliver} />
        <Action id="dismiss" label={t("response.dismiss")} visibleWhen={undelivered}
          confirm={{ title: t("response.dismiss"), body: t("response.dismissConfirm") }} run={dismiss} />
        <Action id="erase" label={t("response.erase")} danger visibleWhen={(row) => !row.erased_at}
          confirm={{ title: t("response.erase"), body: t("response.eraseConfirm") }} run={erase} />
      </Form>
    </ResourceList>
  );
}
