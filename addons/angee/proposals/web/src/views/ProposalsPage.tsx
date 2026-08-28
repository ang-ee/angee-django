import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  InlineEmpty,
  List,
  ResourceList,
  SettingsSection,
  SettingsShell,
  useRuntimeAuth,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "../i18n";
import { useProposalFormDeclaration } from "../proposal-form";
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
  return (
    <DrawerResourceList
      resource={ANSWER_MODEL}
      baseFilter={{ proposal: { exact: recordId } }}
      createDefaults={{ proposal: recordId }}
    >
      <List resource={ANSWER_MODEL} order={{ topic: "ASC" }} emptyContent={t("proposal.answers.empty")}>
        <Column field="topic" />
        <Column field="body" widget="markdown.preview" />
        <Column field="updated_at" header={t("common.updatedAt")} />
      </List>
      <Form resource={ANSWER_MODEL}>
        <Field name="proposal" readOnly />
        <Field name="topic" createOnly />
        <Field name="body" widget="markdown.editor" body />
      </Form>
    </DrawerResourceList>
  );
}

function ProposalReviewsPanel({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProposalsT();
  const { user } = useRuntimeAuth();
  return (
    <SettingsShell maxWidth="1100" gap="8">
      <SettingsSection
        title={t("proposal.reviews.mine.title")}
        description={t("proposal.reviews.mine.description")}
      >
        {user ? (
          <DrawerResourceList
            resource={REVIEW_MODEL}
            baseFilter={{
              proposal: { exact: recordId },
              reviewer: { exact: user.id },
            }}
            createDefaults={{ proposal: recordId, reviewer: user.id }}
          >
            <List resource={REVIEW_MODEL}>
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
        ) : (
          <InlineEmpty label={t("proposal.reviews.mine.signedOut")} />
        )}
      </SettingsSection>
      <SettingsSection
        title={t("proposal.reviews.readable.title")}
        description={t("proposal.reviews.readable.description")}
      >
        <List
          resource={REVIEW_MODEL}
          scope="local"
          baseFilter={{ proposal: { exact: recordId } }}
          order={{ updated_at: "DESC" }}
          emptyContent={t("proposal.reviews.empty")}
        >
          <Column field="reviewer" />
          <Column field="body" widget="markdown.preview" />
          <Column field="updated_at" header={t("common.updatedAt")} />
        </List>
      </SettingsSection>
    </SettingsShell>
  );
}
