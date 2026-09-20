import * as React from "react";
import { Field, Form, Group, registerForm, slotContents, useSlot, type RegisteredFormProps } from "@angee/ui";

import { usePartiesT } from "./i18n";
import { usePartyContactActions } from "./party-contact-actions";
import { PERSON_FORM_FIELDS_SLOT } from "./slots";

const MODEL = "parties.Person";

export function personFields(t: ReturnType<typeof usePartiesT>, extraFields: React.ReactNode): React.ReactNode {
  return <>
    <Field name="display_name" title />
    <Group label={t("person.group.name")} columns={2}>
      <Field name="given_name" label={t("person.field.givenName")} />
      <Field name="family_name" label={t("person.field.familyName")} />
      <Field name="additional_name" label={t("person.field.middleName")} />
      <Field name="nickname" label={t("person.field.nickname")} />
      <Field name="name_prefix" label={t("person.field.prefix")} />
      <Field name="name_suffix" label={t("person.field.suffix")} />
    </Group>
    <Group label={t("person.group.details")} columns={2}>
      <Field name="birthday" label={t("person.field.birthday")} />
      <Field name="anniversary" label={t("person.field.anniversary")} />
      <Field name="folder" label={t("person.folder")} readOnly />
    </Group>
    {extraFields}
    <Field name="notes" />
  </>;
}

/** Canonical Person create/edit form reused by routed and polymorphic relation flows. */
export function PersonForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const extraFields = slotContents(useSlot(PERSON_FORM_FIELDS_SLOT));
  const contactActions = usePartyContactActions();
  return <Form {...props} resource={MODEL}>
    {personFields(t, extraFields)}
    {contactActions}
  </Form>;
}

export const personForm = registerForm(MODEL, PersonForm);
