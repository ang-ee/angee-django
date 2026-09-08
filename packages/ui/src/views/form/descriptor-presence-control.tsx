import type { ReactElement, ReactNode } from "react";

import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import type { FieldDescriptor } from "../page";
import { initialFormSpecValue, type FormSpecFieldDescriptor } from "./form-spec";

/** Shared omission/null affordances around either form owner's native control. */
export function DescriptorPresenceControl({ field, value, readOnly, onChange, onCommit, children }: {
  field: FieldDescriptor;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  children: ReactNode;
}): ReactElement {
  const t = useUiT();
  if ((field.omittable || field.nullable) && value === undefined) {
    return <div className="flex items-center gap-2">
      <span className="text-13 text-fg-3">{t("form.value.notSet")}</span>
      {!readOnly ? <Button type="button" size="sm" variant="secondary"
        onClick={() => { onChange(initialFormSpecValue({ ...field, nullable: field.hasDefault ? field.nullable : false } as FormSpecFieldDescriptor)); onCommit?.(); }}>
        {field.hasDefault ? t("form.value.useDefault") : t("form.value.set")}
      </Button> : null}
      {!readOnly && field.nullable ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
    </div>;
  }
  if (field.nullable && value === null) {
    return <>
      <span className="text-13 text-fg-3">{t("form.value.leftEmpty")}</span>
      {!readOnly ? <div className="mt-1 flex flex-wrap gap-1">
        <Button type="button" size="sm" variant="ghost"
          onClick={() => { onChange(initialFormSpecValue({ ...field, nullable: false, hasDefault: false } as FormSpecFieldDescriptor)); onCommit?.(); }}>
          {t("form.value.set")}
        </Button>
        {field.omittable ? <Button type="button" size="sm" variant="ghost"
          onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.notSet")}</Button> : null}
      </div> : null}
    </>;
  }
  return <>{children}{!readOnly && (field.omittable || field.nullable) ? <div className="mt-1 flex flex-wrap gap-1">
    {field.nullable ? <Button type="button" size="sm" variant="ghost"
      onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
    {field.omittable ? <Button type="button" size="sm" variant="ghost"
      onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.notSet")}</Button> : null}
  </div> : null}</>;
}
