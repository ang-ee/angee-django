import {
  Field,
  Form,
  Group,
  useEnumOptions,
  type WidgetOption,
} from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "./i18n";
import { useProposalCeremonyActions } from "./proposal-actions";
import { PROPOSAL_MODEL } from "./resources";

/** One proposal form declaration reused by the collection and round shell pane. */
export function useProposalFormDeclaration(): React.ReactElement {
  const t = useProposalsT();
  const actions = useProposalCeremonyActions();
  const confidenceOptions = writeEnumOptions(
    useEnumOptions(PROPOSAL_MODEL, "confidence"),
  );
  return (
    <Form resource={PROPOSAL_MODEL} layout="tabs" actions={actions}>
      <Field name="display_name" title readOnly />
      <Field name="state" widget="statusbar" readOnly />
      <Group label={t("proposal.group.identity")} columns={2}>
        <Field name="round" createOnly />
        <Field name="responder" createOnly />
        <Field name="party" createOnly />
        <Field name="source_message" createOnly />
        <Field name="track" readOnly />
      </Group>
      <Group label={t("proposal.group.offer")} columns={2}>
        <Field name="cost" />
        <Field name="currency" />
        <Field name="staffing" />
        <Field name="confidence" options={confidenceOptions} />
        <Field name="timeframe_start" />
        <Field name="timeframe_end" />
        <Field name="valid_until" />
      </Group>
      <Group label={t("proposal.group.receipts")} columns={2}>
        <Field name="submitted_at" readOnly />
        <Field name="submitted_by" readOnly />
        <Field name="decided_at" readOnly />
        <Field name="decided_by" readOnly />
        <Field name="created_at" readOnly />
        <Field name="updated_at" readOnly />
      </Group>
    </Form>
  );
}

export function writeEnumOptions(
  options: readonly WidgetOption[],
): readonly WidgetOption[] {
  return options.map((option) => ({
    ...option,
    value: String(option.value).toLowerCase(),
  }));
}
