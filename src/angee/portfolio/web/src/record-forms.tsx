import { Field, Form, Group, registerForm, useEnumOptions, type RegisteredFormProps } from "@angee/ui";
import { usePortfolioT } from "./i18n";
import { INITIATIVE_MODEL, INITIATIVE_PROJECT_MODEL, PRODUCT_MODEL, RELEASE_MODEL } from "./resources";

function ProductForm(props: RegisteredFormProps): React.ReactElement {
  const t = usePortfolioT();
  const lifecycleOptions = useEnumOptions(PRODUCT_MODEL, "lifecycle");
  return <Form {...props} resource={PRODUCT_MODEL} eyebrow={t("product.eyebrow")}>
    <Field name="name" title />
    <Field name="lifecycle" widget="statusbar" status options={lifecycleOptions} />
    <Group label={t("product.group.provenance")} columns={2}>
      <Field name="owner" />
      <Field name="originated_from" readOnly />
    </Group>
    <Group label={t("common.description")} pane="description" columns={1}>
      <Field name="body" widget="markdown.editor" />
    </Group>
  </Form>;
}

function InitiativeForm(props: RegisteredFormProps): React.ReactElement {
  const t = usePortfolioT();
  const statusOptions = useEnumOptions(INITIATIVE_MODEL, "status");
  const healthOptions = useEnumOptions(INITIATIVE_MODEL, "health");
  const resolutionOptions = useEnumOptions(INITIATIVE_MODEL, "target_date_resolution");
  return <Form {...props} resource={INITIATIVE_MODEL} eyebrow={t("initiative.eyebrow")}>
    <Field name="name" title />
    <Field name="status" widget="statusbar" status options={statusOptions} />
    <Group label={t("initiative.group.identity")} columns={2}>
      <Field name="owner" />
      <Field name="parent" />
      <Field name="target_date" />
      <Field name="priority" />
    </Group>
    <Group label={t("common.description")} pane="description" columns={1}>
      <Field name="body" widget="markdown.editor" />
    </Group>
    <Group label={t("initiative.group.delivery")} pane="delivery" columns={2}>
      <Field name="health" widget="statusBadge" options={healthOptions} readOnly />
      <Field name="health_updated_at" readOnly />
      <Field name="target_date_resolution" label={t("common.datePrecision")} options={resolutionOptions} />
    </Group>
  </Form>;
}

function InitiativeProjectForm(props: RegisteredFormProps): React.ReactElement {
  return <Form {...props} resource={INITIATIVE_PROJECT_MODEL}>
    <Field name="project" title />
    <Field name="initiative" readOnly />
  </Form>;
}

function ReleaseForm(props: RegisteredFormProps): React.ReactElement {
  const t = usePortfolioT();
  const statusOptions = useEnumOptions(RELEASE_MODEL, "status");
  return <Form {...props} resource={RELEASE_MODEL} eyebrow={t("release.eyebrow")}>
    <Field name="name" title />
    <Field name="status" widget="statusbar" status options={statusOptions} />
    <Field name="product" readOnly />
    <Group label={t("release.group.dates")} columns={2}>
      <Field name="target_date" />
      <Field name="shipped_on" />
    </Group>
    <Group label={t("common.description")} pane="description" columns={1}>
      <Field name="notes" widget="markdown.editor" />
    </Group>
  </Form>;
}

export const productForm = registerForm(PRODUCT_MODEL, ProductForm);
export const initiativeForm = registerForm(INITIATIVE_MODEL, InitiativeForm);
export const initiativeProjectForm = registerForm(INITIATIVE_PROJECT_MODEL, InitiativeProjectForm);
export const releaseForm = registerForm(RELEASE_MODEL, ReleaseForm);
