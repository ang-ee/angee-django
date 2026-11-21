import { holdsPermission } from "@angee/metadata";
import {
  Field, FormView, RecordActionBar, canonicalOptionValue, mutationDialogValueCodecs, registerForm,
  useEnumOptions, useRecordChromeActionOutcome, useRecordChromeContext, type RegisteredFormProps,
} from "@angee/ui";

import { useStorageT } from "../i18n";

/** A file opened as a record (a peek or relation dialog) is titled by name, with its preview beneath. */
function FileForm(props: RegisteredFormProps) {
  return <FormView {...props}><Field name="filename" title /></FormView>;
}

export const fileForm = registerForm("storage.File", FileForm);

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
