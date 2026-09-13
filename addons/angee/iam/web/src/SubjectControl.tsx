import { useState, type ReactElement } from "react";
import { Select, SubjectPicker, type MutationDialogControlProps } from "@angee/ui";

import { useIamT } from "./i18n";

/** IAM offers principals and its group subject sets through the shared picker. */
export function SubjectControl({ id, value, readOnly, describedBy, labelledBy, onChange }: MutationDialogControlProps): ReactElement {
  const t = useIamT();
  const selected = typeof value === "string" ? value : "";
  const [chosenResource, setChosenResource] = useState("iam.User");
  const resource = selected.startsWith("auth/group:") ? "iam.Group" : chosenResource;
  return <div className="grid gap-2">
    <Select
      aria-label={t("subject.type")}
      value={resource}
      disabled={readOnly}
      options={[
        { value: "iam.User", label: t("subject.user") },
        { value: "iam.Group", label: t("subject.group") },
      ]}
      onValueChange={(next) => { setChosenResource(next ?? "iam.User"); onChange(""); }}
    />
    <SubjectPicker
      key={resource}
      id={id}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      resource={resource}
      value={selected}
      readOnly={readOnly}
      onChange={onChange}
    />
  </div>;
}
