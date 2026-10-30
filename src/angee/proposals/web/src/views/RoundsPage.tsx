import {
  Column,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
  useRouteHref,
  useRouteRecordId,
  type RecordSmartButtonDescriptor,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useProposalsT } from "../i18n";
import { writeEnumOptions } from "../proposal-form";
import { ROUND_MODEL } from "../resources";

/** Round collection and ceremony record: overview, topic editor, and shells. */
export function RoundsPage(): React.ReactElement {
  const t = useProposalsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordId = useRouteRecordId();
  const openingPolicyOptions = writeEnumOptions(
    useEnumOptions(ROUND_MODEL, "opening_policy"),
  );
  const smartButtons = React.useMemo<readonly RecordSmartButtonDescriptor[]>(
    () => [
      {
        id: "proposals-round-compare",
        icon: "proposals-round",
        count: null,
        label: t("round.compare.open"),
        disabled: !recordId || recordId === "new",
        onClick: () => {
          if (!recordId || recordId === "new") return;
          void navigate({
            to: routeHref("proposals.round-comparison", { id: recordId }),
          });
        },
      },
    ],
    [navigate, recordId, routeHref, t],
  );

  return (
    <ResourceList
      resource={ROUND_MODEL}
      placement="inline"
      routed
      recordSmartButtons={smartButtons}
    >
      <List resource={ROUND_MODEL} defaultGroup={{ field: "status" }} order={{ submission_deadline: "ASC" }}>
        <Column field="name" header={t("common.name")} />
        <Column field="status" header={t("common.status")} widget="statusBadge" />
        <Column field="task" />
        <Column field="project" />
        <Column field="facilitator" />
        <Column field="last_call_at" />
        <Column field="submission_deadline" />
      </List>
      <Form resource={ROUND_MODEL} eyebrow={t("round.eyebrow")}>
        <Field name="permissions" hidden readOnly />
        <Field name="revision" hidden readOnly />
        <Field name="name" title />
        <Group columns={2}>
          <Field name="team" />
          <Field name="roster_visibility" />
          <Field name="clarification_askers" />
          <Field name="opens_after" />
          <Field name="clarifications_shared_until" />
        </Group>
        <Field name="status" widget="statusbar" status readOnly />
        {recordId === "new" ? (
          <>
            <Group label={t("round.group.target")} columns={2}>
              <Field name="task" createOnly />
              <Field name="project" createOnly />
              <Field name="facilitator" createOnly />
              <Field name="requester_party" createOnly />
            </Group>
            <Group label={t("round.group.ceremony")} columns={2}>
              <Field name="last_call_at" createOnly />
              <Field name="submission_deadline" createOnly />
            </Group>
          </>
        ) : (
          <>
            <Group label={t("round.group.target")} columns={2}>
              <Field name="task" createOnly />
              <Field name="project" createOnly />
              <Field name="requester_party" />
            </Group>
            <Group label={t("round.group.ceremony")} columns={2}>
              <Field name="facilitator" createOnly />
              <Field name="opening_policy" options={openingPolicyOptions} />
              <Field name="last_call_at" />
              <Field name="submission_deadline" />
              <Field name="outcome" readOnly />
            </Group>
            <Group label={t("round.group.receipts")} columns={2}>
              <Field name="opened_at" readOnly />
              <Field name="opened_by" readOnly />
              <Field name="closed_at" readOnly />
              <Field name="closed_by" readOnly />
              <Field name="created_at" readOnly />
              <Field name="updated_at" readOnly />
            </Group>
          </>
        )}
      </Form>
    </ResourceList>
  );
}

