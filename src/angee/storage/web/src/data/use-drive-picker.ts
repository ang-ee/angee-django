import { useModelMetadata } from "@angee/metadata";
import { relationFieldInfoForResource, useRelationOptions, useRelationPickerOptions } from "@angee/ui";
import { useMemo } from "react";

const DRIVE_MODEL = "storage.Drive";

/** Storage's initial drive selection composes the same remote relation owner in both upload surfaces. */
export function useDrivePicker(value: string | null | undefined, enabled = true) {
  const metadata = useModelMetadata(DRIVE_MODEL);
  const relation = useMemo(() => enabled
    ? relationFieldInfoForResource(DRIVE_MODEL, metadata ?? null) : null, [enabled, metadata]);
  const first = useRelationOptions(relation, { enabled: !value, pageSize: 1, sort: true });
  const selectedId = value || first.options[0]?.value || "";
  const picker = useRelationPickerOptions(relation, {
    value: selectedId, selectedOption: first.options.find((option) => option.value === selectedId),
    pageSize: 50, sort: true,
  });
  const roots = useMemo(() => picker.options.map((option) => ({ id: option.value, name: option.label })), [picker.options]);
  return { selectedId, picker, roots, initial: first.list };
}
