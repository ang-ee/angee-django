import * as React from "react";
import { useDebounce } from "use-debounce";
import { rowPublicId, rowValueAtPath, useModelMetadata } from "@angee/metadata";

import { ErrorBanner } from "../../fragments/ErrorBanner";
import { useUiT } from "../../i18n";
import { RelationPicker, type RelationPickerProps } from "../relation/RelationPicker";
import { useRelationOptions } from "../relation/relation-options";
import type { RelationOption } from "../../widgets/RelationField";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";

export interface SubjectPickerProps extends Pick<RelationPickerProps,
  "id" | "controlRef" | "aria-label" | "aria-labelledby" | "aria-describedby" | "aria-required" | "readOnly"
> {
  resource: string;
  value: string;
  onChange: (subject: string) => void;
}

/** Select a canonical subject through its resource's declared subject field. */
export function SubjectPicker({ resource, value, onChange, readOnly, ...control }: SubjectPickerProps): React.ReactElement {
  const t = useUiT();
  const model = useModelMetadata(resource);
  const subjectField = model?.resource.subjectField;
  const available = Boolean(subjectField && model?.resource.roots.list);
  const info = React.useMemo(() => relationFieldInfoForResource(resource, model), [resource, model]);
  const fields = React.useMemo(() => subjectField ? [subjectField] : [], [subjectField]);
  const [opened, setOpened] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const [searchText] = useDebounce(search, 250);
  // The picker needs a public row id and label; the form owns its canonical
  // subject. Preserve the selected option when a later search changes the page.
  const [selection, setSelection] = React.useState<{ subject: string; option: RelationOption } | null>(null);
  const { list, options, rows } = useRelationOptions(info, { enabled: opened && available, fields, searchText });
  const loadedSelection = React.useMemo(() => {
    if (!subjectField || !value) return undefined;
    const row = rows.find((item) => rowValueAtPath(item, subjectField) === value);
    const id = row ? rowPublicId(row) : null;
    return id ? options.find((option) => option.value === id) : undefined;
  }, [options, rows, subjectField, value]);
  const selected = selection?.subject === value ? selection.option : loadedSelection;
  const visibleOptions = React.useMemo(() => selected && !options.some((option) => option.value === selected.value)
    ? [selected, ...options] : options, [options, selected]);

  if (!available || !info) return <ErrorBanner description={t("access.unavailableSubject")} />;
  return <RelationPicker
    {...control}
    value={selected?.value ?? ""}
    options={visibleOptions}
    readOnly={readOnly}
    onOpenChange={(open) => { setSearch(""); setOpened(open); }}
    onSearchChange={setSearch}
    searchState={{ pending: list.fetching || search !== searchText, error: list.error, retry: list.refetch }}
    onChange={(id) => {
      const row = rows.find((item) => rowPublicId(item) === id);
      const subject = row && subjectField ? rowValueAtPath(row, subjectField) : null;
      const option = options.find((item) => item.value === id);
      setSelection(typeof subject === "string" && option ? { subject, option } : null);
      onChange(typeof subject === "string" ? subject : "");
    }}
  />;
}
