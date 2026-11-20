import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Column, Field, Form, Group, List, ResourceList, useRecordActionMutation,
  type ResourceListProps,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL, RESPONSE_MODEL } from "../resources";
import { isUndelivered, undeliveredFilter } from "../undelivered";

/**
 * The responses forms have kept, read only: each is evidence, as received. What
 * became of one changes only through its owner's verbs: hand it to the form's
 * target again, give up on it, or blank the answers its version marks as personal.
 * A delivery that fails is tried again by itself until it runs out of attempts;
 * the record shows how often it was tried, why it last failed and when it is next tried.
 * The list filters by what became of a response, with one choice for those still
 * waiting (pending and failed together), and a response its form's retention will
 * remove shows the day it is removed.
 */
export function ResponsesPage(): React.ReactElement {
  const t = useWebformsT();
  const filterOptions = React.useMemo<NonNullable<ResourceListProps["filterOptions"]>>(() => [
    { id: "undelivered", label: t("response.undelivered"), filter: undeliveredFilter() },
  ], [t]);
  // Delivering or dismissing a response changes how many its form still counts as undelivered.
  // A delivery that fails answers with the reason and still records the attempt on the response.
  const [deliver] = useRecordActionMutation<ActionFieldName>("deliver_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL, FORM_MODEL], invalidateOnFailure: true,
  });
  const [dismiss] = useRecordActionMutation<ActionFieldName>("dismiss_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL, FORM_MODEL],
  });
  const [erase] = useRecordActionMutation<ActionFieldName>("erase_webform_response", {
    idArgument: "response", invalidateModels: [RESPONSE_MODEL],
  });
  return (
    <ResourceList resource={RESPONSE_MODEL} hideCreate placement="inline" routed filterOptions={filterOptions}>
      <List resource={RESPONSE_MODEL} order={{ created_at: "DESC" }} emptyContent={t("response.empty")}>
        <Column field="form" />
        <Column field="version" />
        <Column field="language" />
        <Column field="delivery" widget="statusBadge" />
        <Column field="delivery_error" />
        <Column field="created_at" />
        <Column field="expires_at" header={t("response.expires")} />
      </List>
      <Form resource={RESPONSE_MODEL} readOnly>
        <Field name="delivery" widget="statusbar" status />
        <Group columns={2}>
          <Field name="form" />
          <Field name="version" />
          <Field name="language" />
          <Field name="created_at" />
          <Field name="delivered_at" showWhen={(row) => Boolean(row.delivered_at)} />
          <Field name="delivery_attempts" />
          <Field name="delivery_error" showWhen={(row) => Boolean(row.delivery_error)} />
          <Field name="retry_at" showWhen={(row) => Boolean(row.retry_at)} />
          <Field name="erased_at" showWhen={(row) => Boolean(row.erased_at)} />
          <Field name="expires_at" label={t("response.expires")} showWhen={(row) => Boolean(row.expires_at)} />
        </Group>
        <Field name="answers" widget="json" />
        <Action id="deliver" label={t("response.deliver")} primary visibleWhen={isUndelivered} run={deliver} />
        <Action id="dismiss" label={t("response.dismiss")} visibleWhen={isUndelivered}
          confirm={{ title: t("response.dismiss"), body: t("response.dismissConfirm") }} run={dismiss} />
        <Action id="erase" label={t("response.erase")} danger visibleWhen={(row) => !row.erased_at}
          confirm={{ title: t("response.erase"), body: t("response.eraseConfirm") }} run={erase} />
      </Form>
    </ResourceList>
  );
}
