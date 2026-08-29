import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
  useRouteHref,
  useRouteRecordId,
  type RecordPanelContext,
  type RecordSmartButtonDescriptor,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useProposalsT } from "../i18n";
import {
  useProposalFormDeclaration,
  writeEnumOptions,
} from "../proposal-form";
import { useRoundCeremonyActions } from "../round-actions";
import { PROPOSAL_MODEL, ROUND_MODEL, TOPIC_MODEL } from "../resources";

interface ProposalShellRow extends StringIdRow {
  state?: unknown;
}

/** Round collection and ceremony record: overview, topic editor, and shells. */
export function RoundsPage(): React.ReactElement {
  const t = useProposalsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordId = useRouteRecordId();
  const openingPolicyOptions = writeEnumOptions(
    useEnumOptions(ROUND_MODEL, "opening_policy"),
  );
  const actions = useRoundCeremonyActions(recordId ?? "");
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "topics",
        label: t("round.tabs.topics"),
        icon: "proposals-response",
        render: (context) => <RoundTopicsPanel {...context} />,
      },
      {
        id: "proposals",
        label: t("round.tabs.proposals"),
        icon: "proposals-round",
        render: (context) => <RoundProposalsPanel {...context} />,
      },
    ],
    [t],
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
      recordTabs={recordTabs}
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
      <Form
        resource={ROUND_MODEL}
        layout="tabs"
        actions={[actions.open, actions.close, actions.transfer, actions.cancel]}
      >
        <Field name="name" title />
        <Field name="status" widget="statusbar" readOnly />
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

function RoundTopicsPanel({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProposalsT();
  return (
    <DrawerResourceList
      resource={TOPIC_MODEL}
      baseFilter={{ round: { exact: recordId } }}
      createDefaults={{ round: recordId }}
    >
      <List resource={TOPIC_MODEL} order={{ sort_order: "ASC" }} emptyContent={t("round.topics.empty")}>
        <Column field="sort_order" />
        <Column field="name" header={t("common.name")} />
        <Column field="key" />
        <Column field="hint" widget="markdown.preview" />
      </List>
      <Form resource={TOPIC_MODEL}>
        <Field name="name" title />
        <Group columns={2}>
          <Field name="round" readOnly />
          <Field name="key" createOnly />
          <Field name="sort_order" />
        </Group>
        <Field name="hint" widget="markdown.editor" body />
      </Form>
    </DrawerResourceList>
  );
}

function RoundProposalsPanel({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const form = useProposalFormDeclaration();
  return (
    <DrawerResourceList
      resource={PROPOSAL_MODEL}
      baseFilter={{ round: { exact: recordId } }}
      createDefaults={{ round: recordId }}
    >
      <List<ProposalShellRow>
        resource={PROPOSAL_MODEL}
        order={{ created_at: "ASC" }}
        rowHref={(row) => routeHref("proposals.proposals.record", { id: row.id })}
        emptyContent={t("round.proposals.empty")}
      >
        <Column field="display_name" />
        <Column field="responder" header={t("common.responder")} />
        <Column field="party" header={t("common.party")} />
        <Column field="state" header={t("common.state")} widget="statusBadge" />
        <Column field="submitted_at" />
        <Column field="track" />
      </List>
      {form}
    </DrawerResourceList>
  );
}
