import type { ReactElement, ReactNode } from "react";

import { Avatar, avatarInitials } from "../ui/avatar";
import { Select } from "../ui/select";
import { textRoleVariants } from "../ui/text";
import { widgetLabel } from "./label";
import {
  optionTextLabel,
  type WidgetDefinition,
  type WidgetOption,
  type WidgetRenderProps,
} from "./types";

type OwnerCellRecord = {
  id?: string;
  value?: string;
  label?: ReactNode;
  name?: string;
  avatarUrl?: string;
  src?: string;
};
type OwnerCellValue = string | OwnerCellRecord | null;

function OwnerCellEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<OwnerCellValue>): ReactElement {
  if (readOnly || (field?.options?.length ?? 0) === 0) {
    return <OwnerCellRead value={value} field={field} />;
  }

  return (
    <Select
      value={ownerKey(value)}
      options={field?.options ?? []}
      readOnly={readOnly}
      disabled={readOnly}
      aria-label={widgetLabel(field, "Owner")}
      placeholder={widgetLabel(field, "Owner")}
      onValueChange={(next) => onChange?.(next)}
    />
  );
}

function OwnerCellRead({
  value,
  field,
}: WidgetRenderProps<OwnerCellValue>): ReactElement {
  const label = ownerLabel(value, field?.options ?? []);
  const text = ownerText(value, label);
  const src =
    typeof value === "object" && value ? value.src ?? value.avatarUrl : undefined;

  if (!text) return <span className={textRoleVariants({ role: "meta" })} />;

  return (
    <span className="inline-flex min-w-0 items-center gap-2 text-13 text-fg">
      <Avatar
        size="sm"
        src={src}
        alt={text}
        initials={avatarInitials(text)}
        className="shrink-0"
      />
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

export const ownerCellWidget = {
  edit: OwnerCellEdit,
  read: OwnerCellRead,
  cell: OwnerCellRead,
} satisfies WidgetDefinition<OwnerCellValue>;

function ownerKey(value: OwnerCellValue | undefined): string {
  if (typeof value === "string") return value;
  return value?.value ?? value?.id ?? "";
}

function ownerLabel(
  value: OwnerCellValue | undefined,
  options: readonly WidgetOption[],
): ReactNode {
  if (typeof value === "string") {
    return options.find((option) => option.value === value)?.label ?? value;
  }
  if (!value) return "";
  return value.label ?? value.name ?? value.value ?? value.id ?? "";
}

function ownerText(value: OwnerCellValue | undefined, label: ReactNode): string {
  const text = optionTextLabel(label, "");
  if (text) return text;
  if (typeof value === "object" && value) {
    return value.name ?? value.value ?? value.id ?? "";
  }
  return "";
}
