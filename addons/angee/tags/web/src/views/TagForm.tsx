import * as React from "react";
import { Field, Form, Group, containerContents, registerForm, useContainer, type RegisteredFormProps } from "@angee/ui";

import { useTagsT } from "../i18n";

const TAG_MODEL = "tags.Tag";

/**
 * The tag form, shared by the Tags page and a relation picker's "Create “…”":
 * the name as its title, its colour and any scope-specific fields
 * (`tags.tags#fields`), and, on a saved tag only, its archive flag.
 */
function TagForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useTagsT();
  const scopeFieldEntries = useContainer("tags.tags#fields");
  return (
    <Form {...props} resource={TAG_MODEL}>
      <Field name="name" title />
      <Group label={t("form.details")} columns={2}>
        <Field name="color" label={t("col.color")} />
        {containerContents(scopeFieldEntries)}
      </Group>
      <Group savedOnly>
        <Field name="is_archived" />
      </Group>
    </Form>
  );
}

export const tagForm = registerForm(TAG_MODEL, TagForm);
