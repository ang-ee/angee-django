import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  List,
  useRecordChromeContext,
  useRouteHref,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "./i18n";
import { useProposalFormDeclaration } from "./proposal-form";
import { PROPOSAL_MODEL, TOPIC_MODEL } from "./resources";

interface ProposalShellRow extends StringIdRow {
  state?: unknown;
}

/** The round's topics, as the record's own `#sections` tab. */
export function RoundTopicsPanel(): React.ReactElement {
  const t = useProposalsT();
  const { recordId } = useRecordChromeContext();
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

/** The round's proposal shells, as the record's own `#sections` tab. */
export function RoundProposalsPanel(): React.ReactElement {
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const { recordId } = useRecordChromeContext();
  const form = useProposalFormDeclaration();
  return (
    <DrawerResourceList
      resource={PROPOSAL_MODEL}
      presentation="embedded"
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
