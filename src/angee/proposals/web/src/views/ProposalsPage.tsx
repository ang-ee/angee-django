import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useRuntimeAuth,
  useEnumOptions,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "../i18n";
import { ANSWER_VISIBILITY } from "../documents";
import { useAnswerActions } from "../answer-actions";
import { useProposalFormDeclaration, writeEnumOptions } from "../proposal-form";
import {
  ANSWER_MODEL,
  PROPOSAL_MODEL,
  REVIEW_MODEL,
} from "../resources";

/** Proposal collection and responder record with answer/review work panes. */
export function ProposalsPage(): React.ReactElement {
  const t = useProposalsT();
  const form = useProposalFormDeclaration();
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "answers",
        label: t("proposal.tabs.answers"),
        icon: "proposals-response",
        render: (context) => <ProposalAnswersPanel {...context} />,
      },
      {
        id: "reviews",
        label: t("proposal.tabs.reviews"),
        icon: "proposals-response",
        render: (context) => <ProposalReviewsPanel {...context} />,
      },
    ],
    [t],
  );
  return (
    <ResourceList
      resource={PROPOSAL_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
    >
      <List resource={PROPOSAL_MODEL} defaultGroup={{ field: "state" }} order={{ updated_at: "DESC" }}>
        <Column field="display_name" />
        <Column field="round.name" />
        <Column field="responder" header={t("common.responder")} />
        <Column field="party" header={t("common.party")} />
        <Column field="state" header={t("common.state")} widget="statusBadge" />
        <Column field="cost" />
        <Column field="confidence" />
        <Column field="submitted_at" />
      </List>
      {form}
    </ResourceList>
  );
}

function ProposalAnswersPanel({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProposalsT();
  const actions = useAnswerActions();
  const visibilityOptions = writeEnumOptions(useEnumOptions(ANSWER_MODEL, "visibility"));
  const actionVisibilityOptions = useEnumOptions(ANSWER_MODEL, "visibility", { casing: "upper" });
  return (
    <DrawerResourceList
      resource={ANSWER_MODEL}
      presentation="embedded"
      baseFilter={{ proposal: { exact: recordId } }}
      createDefaults={{ proposal: recordId }}
    >
      <List resource={ANSWER_MODEL} order={{ topic: "ASC" }} emptyContent={t("proposal.answers.empty")}>
        <Column field="topic" />
        <Column field="body" widget="markdown.preview" />
        <Column field="updated_at" header={t("common.updatedAt")} />
      </List>
      <Form resource={ANSWER_MODEL} actions={actions}>
        <Field name="permissions" hidden readOnly />
        <Field name="revision" hidden readOnly />
        <Field name="proposal" readOnly />
        <Field name="topic" title createOnly />
        <Field name="allowed_visibility" hidden readOnly />
        <Field name="visibility" widget="visibility" placement="title" options={visibilityOptions}
          visibilityAction={{ document: ANSWER_VISIBILITY, resultField: "set_proposal_answer_visibility",
            idArgument: "answer", revisionArgument: "revision", options: actionVisibilityOptions }} />
        <Field name="shared_with_responders" readOnly />
        <Field name="body" widget="markdown.editor" body />
      </Form>
    </DrawerResourceList>
  );
}

/**
 * Every review the server returns for this proposal, as one list. "Mine" narrows
 * it to the viewer's own review, which the viewer creates and edits here; other
 * reviewers' rows open read-only through their projected permissions.
 */
export function ProposalReviewsPanel({ recordId }: Pick<RecordPanelContext, "recordId">): React.ReactElement {
  const t = useProposalsT();
  const { user } = useRuntimeAuth();
  const mine = React.useMemo(
    () => user ? [{ id: "mine", label: t("proposal.reviews.mine"), filter: { reviewer: { exact: user.id } } }] : [],
    [t, user],
  );
  return (
    <DrawerResourceList
      resource={REVIEW_MODEL}
      presentation="embedded"
      baseFilter={{ proposal: { exact: recordId } }}
      createDefaults={user ? { proposal: recordId, reviewer: user.id } : undefined}
      hideCreate={!user}
    >
      <List
        resource={REVIEW_MODEL}
        order={{ updated_at: "DESC" }}
        emptyContent={t("proposal.reviews.empty")}
        filterOptions={mine}
        search={user ? { shortcuts: [{ kind: "toggle", id: "mine" }] } : undefined}
        chrome={user ? { search: true } : undefined}
      >
        <Column field="reviewer" />
        <Column field="body" widget="markdown.preview" />
        <Column field="updated_at" header={t("common.updatedAt")} />
      </List>
      <Form resource={REVIEW_MODEL}>
        <Group columns={2}>
          <Field name="proposal" readOnly />
          <Field name="reviewer" readOnly />
        </Group>
        <Field name="body" widget="markdown.editor" body />
      </Form>
    </DrawerResourceList>
  );
}
