import * as React from "react";
import {
  Action, Field, Form, Group, ListView, registerForm, containerContents, useContainer,
  type ListColumn, type RecordPanelContext, type RecordTabDescriptor, type RegisteredFormProps, type StringIdRow,
} from "@angee/ui";

import { PersonCirclesList } from "./CircleMembershipList";
import { IdentityTab } from "./IdentityTab";
import { PartyAddresses } from "./PartyAddresses";
import { PersonRelationshipsTab } from "./PersonRelationships";
import { usePartiesT } from "./i18n";
import { useMergeIntoAction } from "./merge-into";
import { renderPartyUnavailable } from "./PartyRecordRedirect";
import { usePartyContactActions } from "./party-contact-actions";

const MODEL = "parties.Person";

type HandleRow = StringIdRow;

/** The person's handles: an embedded list filtered to this party. */
function PersonHandlesTab({ recordId }: Pick<RecordPanelContext, "recordId">): React.ReactElement {
  const t = usePartiesT();
  const columns = React.useMemo<readonly ListColumn<HandleRow>[]>(() => [
    { field: "platform" },
    { field: "value", render: (row) => <span className="font-medium text-fg">{String(row.value ?? "")}</span> },
    { field: "label" },
    { field: "is_preferred", header: t("person.handlePreferred"), widget: "booleanBadge" },
  ], [t]);
  return (
    <ListView<HandleRow>
      resource="parties.Handle"
      presentation="embedded"
      chrome={{ heading: false }}
      fields={["id", "platform", "value", "label", "is_preferred"]}
      baseFilter={{ party: { exact: recordId } }}
      columns={columns}
      emptyContent={t("person.empty.handles")}
    />
  );
}

function personTabs(t: ReturnType<typeof usePartiesT>): readonly RecordTabDescriptor[] {
  return [
    { id: "handles", label: t("person.tabs.handles"), render: (context) => <PersonHandlesTab {...context} /> },
    { id: "identity", label: t("person.tabs.identity"), render: (context) => <IdentityTab {...context} /> },
    { id: "circles", label: t("person.tabs.circles"), render: ({ recordId }) => <PersonCirclesList personId={recordId} /> },
    { id: "relationships", label: t("person.tabs.relationships"), render: (context) => <PersonRelationshipsTab {...context} /> },
    { id: "addresses", label: t("person.tabs.addresses"), render: (context) => <PartyAddresses {...context} /> },
  ];
}

export function personFields(t: ReturnType<typeof usePartiesT>, extraFields: React.ReactNode): React.ReactNode {
  return <>
    <Field name="display_name" title />
    <Group label={t("person.group.name")} columns={2}>
      <Field name="given_name" label={t("person.field.givenName")} />
      <Field name="family_name" label={t("person.field.familyName")} />
    </Group>
    <Field name="tags" />
    <Group label={t("person.group.about")} columns={2}>
      <Field name="occupation" label={t("person.field.occupation")} />
      <Field name="languages" label={t("person.field.languages")} />
    </Group>
    <Group label={t("person.group.details")} pane="personal-details" columns={2}>
      <Field name="additional_name" label={t("person.field.middleName")} />
      <Field name="nickname" label={t("person.field.nickname")} />
      <Field name="name_prefix" label={t("person.field.prefix")} />
      <Field name="name_suffix" label={t("person.field.suffix")} />
      <Field name="birthday" label={t("person.field.birthday")} />
      <Field name="anniversary" label={t("person.field.anniversary")} />
    </Group>
    {extraFields}
    <Group label={t("common.notes")} pane="notes" columns={1}>
      <Field name="notes" />
    </Group>
  </>;
}

/**
 * The one Person form: fields, contact verbs, the record tabs (handles, identity,
 * circles, relationships, addresses) and the "Merge into…" verb that opens the
 * comparison. Routed and polymorphic relation flows all render it.
 */
export function PersonForm({ resource: _resource, recordTabs, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const extraFields = containerContents(useContainer("parties.person#fields"));
  const contactActions = usePartyContactActions();
  const mergeInto = useMergeIntoAction();
  const tabs = React.useMemo(() => personTabs(t), [t]);
  return <Form {...props} resource={MODEL} eyebrow={t("person.eyebrow")} recordTabs={recordTabs ?? tabs}
    renderUnavailable={renderPartyUnavailable}>
    {personFields(t, extraFields)}
    {contactActions}
    <Action {...mergeInto} />
  </Form>;
}

export const personForm = registerForm(MODEL, PersonForm);
