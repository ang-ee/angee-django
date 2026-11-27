import { holdsPermission } from "@angee/metadata";
import {
  Button, Field, FormView, Glyph, Group, RecordActionBar, canonicalOptionValue, mutationDialogValueCodecs, registerForm,
  useEnumOptions, useRecordChromeActionOutcome, useRecordChromeContext, type RegisteredFormProps,
} from "@angee/ui";

import { useStorageT } from "../i18n";

/** One complete File form owns its title, metadata, preview and record actions everywhere. */
function FileForm(props: RegisteredFormProps) {
  const t = useStorageT();
  return <FormView defaultRecordTab="preview" submitLabel={t("file.rename")}
    {...props} returning={["created_at", "updated_at", ...(props.returning ?? [])]}>
    <Field name="title" widget="text" title
      resolve={(values) => ({ placeholder: typeof values.filename === "string" ? values.filename : undefined })} />
    <Group label={t("file.details")} pane="details" columns={2}>
      <Field name="filename" label={t("file.filename")} readOnly />
      <Field name="created_by_label" label={t("file.author")} widget="userRef" readOnly />
      <Field name="upload_state" label={t("file.stage")} readOnly />
      <Field name="visibility" label={t("file.visibility")} widget="statusBadge" readOnly />
      <Field name="tags" />
    </Group>
  </FormView>;
}

export const fileForm = registerForm("storage.File", FileForm);

/** File download shares the form's primary action row and permission-scoped record. */
export function DownloadFileAction() {
  const t = useStorageT();
  const { record } = useRecordChromeContext();
  if (!record || record.is_trashed || !record.url) return null;
  return <Button size="sm" variant="primary" nativeButton={false} role="link"
    render={<a href={String(record.url)} download={String(record.filename)} />}>
    <Glyph name="download" />{t("file.download")}
  </Button>;
}

/** The file's audience verb, shared by routed preview chrome and registered record forms. */
export function ChangeFileVisibilityAction() {
  const t = useStorageT();
  const { record, recordId, formReadOnly } = useRecordChromeContext();
  const visibilityOptions = useEnumOptions("storage.File", "visibility", { casing: "upper" });
  const [change, state] = useRecordChromeActionOutcome("set_file_visibility");
  const options = visibilityOptions.filter(({ value }) => record && (
    (value === "INHERITED" && holdsPermission(record, "inherit_visibility"))
    || (value === "RECORD" && holdsPermission(record, "write"))
  ));
  if (!record || !recordId || formReadOnly || options.length === 0) return null;
  return <RecordActionBar record={record} actions={[{
    id: "change-visibility", label: t("file.changeVisibility"), placement: "menu", disabled: state.fetching,
    args: [{ name: "visibility", label: t("file.visibility"), widget: "select", options,
      fromContext: ({ record: current }) => canonicalOptionValue(options, current?.visibility) }],
    submit: (values) => change(recordId, {
      visibility: mutationDialogValueCodecs.requiredString(canonicalOptionValue(options, values.visibility), "visibility"),
    }),
  }]} />;
}
