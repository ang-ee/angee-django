import * as React from "react";
import { Field, Form, Group, registerForm, containerContents, useContainer, type RecordTabDescriptor, type RegisteredFormProps } from "@angee/ui";

import { usePartiesT } from "./i18n";
import { PartyAddresses } from "./PartyAddresses";
import { IdentityTab } from "./IdentityTab";
import { usePartyContactActions } from "./party-contact-actions";

const MODEL = "parties.Organization";

function organizationTabs(t: ReturnType<typeof usePartiesT>): readonly RecordTabDescriptor[] {
  return [
    {
      id: "identity",
      label: t("organization.tabs.identity"),
      render: (context) => <IdentityTab {...context} />,
    },
    {
      id: "addresses",
      label: t("organization.tabs.addresses"),
      render: (context) => <PartyAddresses {...context} />,
    },
  ];
}

/**
 * Organization identity fields shared by organizations and their specializations.
 * Form parses its children statically, so return field elements from a hook
 * instead of hiding the declarations behind a rendered component.
 */
export function useOrganizationFields(): React.ReactElement {
  const t = usePartiesT();
  const extraFields = useContainer("parties.organization#fields");
  return (
    <>
      <Field name="display_name" title />
      <Group label={t("organization.group.details")} columns={2}>
        <Field name="legal_name" label={t("organization.field.legalName")} />
        <Field name="domain" label={t("organization.field.domain")} />
      </Group>
      {containerContents(extraFields)}
      <Field name="notes" />
    </>
  );
}

/** The canonical organization form, reused by routed and inline relation flows. */
export function OrganizationForm({ resource: _resource, recordTabs, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const fields = useOrganizationFields();
  const contactActions = usePartyContactActions();
  return (
    <Form {...props} resource={MODEL} recordTabs={recordTabs ?? organizationTabs(t)}>
      {fields}
      <Field name="tags" />
      {contactActions}
    </Form>
  );
}

export const organizationForm = registerForm(MODEL, OrganizationForm);
