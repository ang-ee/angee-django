import * as React from "react";
import { useDebounce } from "use-debounce";
import {
  modelLabelSegment,
  rowPublicId,
  rowValueAtPath,
  useModelMetadata,
  type DataResourceGrantableRelation,
  type DataResourceSubjectSpecies,
} from "@angee/metadata";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { LoadingPanel } from "../../fragments/LoadingPanel";
import { useUiT } from "../../i18n";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { Button } from "../../ui/button";
import { FieldLabel, FieldRoot } from "../../ui/field";
import { Select } from "../../ui/select";
import { RelationPicker } from "../relation/RelationPicker";
import { useRelationOptions } from "../relation/relation-options";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { defineRowAction } from "../resource/RowActions";
import { RowsListView } from "../resource/RowsListView";

/** Presentation contract; the contributing addon owns its typed API adapter. */
export type RecordAccessEntry = {
  id: string;
  targetId: string;
  relation: string;
  subject: string;
  subjectType: string;
  label: string;
};

export interface ManageAccessDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: React.ReactElement;
  label: string;
  targetIds: readonly string[];
  grantable: readonly DataResourceGrantableRelation[];
  entries: readonly RecordAccessEntry[];
  fetching: boolean;
  error: Error | null;
  onRetry: () => void;
  onGrant: (relation: string, subject: string) => Promise<boolean>;
  onRevoke: (entry: RecordAccessEntry) => Promise<void>;
}

/** Direct access for one record or a selection, over the shared collection owners. */
export function ManageAccessDialog(props: ManageAccessDialogProps): React.ReactElement {
  const t = useUiT();
  return (
    <DialogForm
      open={props.open}
      onOpenChange={props.onOpenChange}
      trigger={props.trigger}
      title={t("access.title", { label: props.label })}
      description={t("access.directOnly")}
      size="lg"
    >
      {props.open ? <AccessContents {...props} /> : null}
    </DialogForm>
  );
}

function AccessContents({
  targetIds, grantable, entries, fetching, error, onRetry, onGrant, onRevoke,
}: ManageAccessDialogProps): React.ReactElement {
  const t = useUiT();
  const [relationName, setRelationName] = React.useState(grantable[0]?.relation ?? "");
  const [speciesKey, setSpeciesKey] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [pickerRevision, setPickerRevision] = React.useState(0);
  const [pending, setPending] = React.useState(false);
  const relation = grantable.find((item) => item.relation === relationName) ?? grantable[0];
  const species = relation?.subjects.filter(
    (item): item is DataResourceSubjectSpecies & { resource: string } => Boolean(item.resource),
  ) ?? [];
  const selectedSpecies = species.find((item) => subjectSpeciesKey(item) === speciesKey) ?? species[0];
  const relationLabelId = React.useId();
  const speciesLabelId = React.useId();
  const rowActions = React.useMemo(() => [defineRowAction<RecordAccessEntry>({
    kind: "page",
    id: "revoke",
    label: t("access.remove"),
    icon: "x",
    variant: "ghost",
    pendingPolicy: "disable-actions",
    disabled: () => pending || fetching,
    onSelect: async (entry) => {
      setPending(true);
      try {
        await onRevoke(entry);
      } finally {
        setPending(false);
      }
    },
  })], [fetching, onRevoke, pending, t]);
  const columns = React.useMemo(() => [
    { field: "label", label: t("access.recipient") },
    { field: "relation", label: t("access.relation") },
    ...(targetIds.length > 1 ? [{ field: "targetId", label: t("access.record") }] : []),
  ], [t, targetIds.length]);

  return (
    <ControlBandProvider host={undefined}>
      <ErrorBanner description={error?.message ?? null} actions={error ? (
        <Button size="sm" onClick={onRetry}>{t("collection.retry")}</Button>
      ) : undefined} />
      {fetching && entries.length === 0 ? <LoadingPanel density="inline" /> : null}
      <div className="grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <FieldRoot>
              <FieldLabel id={relationLabelId} nativeLabel={false} render={<span />}>
                {t("access.relation")}
              </FieldLabel>
              <Select
                aria-labelledby={relationLabelId}
                value={relation?.relation ?? ""}
                disabled={pending || fetching || Boolean(error)}
                options={grantable.map((item) => ({ value: item.relation, label: item.relation }))}
                onValueChange={(value) => { setRelationName(value ?? ""); setSpeciesKey(""); setSubject(""); }}
              />
            </FieldRoot>
            <FieldRoot>
              <FieldLabel id={speciesLabelId} nativeLabel={false} render={<span />}>
                {t("access.recipientType")}
              </FieldLabel>
              <Select
                aria-labelledby={speciesLabelId}
                value={selectedSpecies ? subjectSpeciesKey(selectedSpecies) : ""}
                disabled={pending || fetching || Boolean(error) || species.length === 0}
                options={species.map((item) => ({
                  value: subjectSpeciesKey(item),
                  label: `${modelLabelSegment(item.resource)}${item.relation ? ` (${item.relation})` : ""}`,
                }))}
                onValueChange={(value) => { setSpeciesKey(value ?? ""); setSubject(""); }}
              />
            </FieldRoot>
          </div>
          {selectedSpecies ? <SubjectPicker
            key={`${relation?.relation}:${subjectSpeciesKey(selectedSpecies)}:${pickerRevision}`}
            species={selectedSpecies}
            disabled={pending || fetching || Boolean(error)}
            onChange={setSubject}
          /> : null}
          <Button
            type="button" variant="primary" size="sm"
            disabled={pending || fetching || Boolean(error) || !subject || !relation || targetIds.length === 0}
            onClick={async () => {
              if (!relation || !subject) return;
              setPending(true);
              try {
                if (await onGrant(relation.relation, subject)) {
                  setSubject("");
                  setPickerRevision((value) => value + 1);
                }
              } finally {
                setPending(false);
              }
            }}
          >{t("access.add")}</Button>
        </div>
      {!error ? <RowsListView
        scope="local"
        presentation="embedded"
        rows={entries}
        fetching={fetching}
        columns={columns}
        rowActions={rowActions}
        emptyContent={t("access.empty")}
      /> : null}
    </ControlBandProvider>
  );
}

function subjectSpeciesKey(species: DataResourceSubjectSpecies): string {
  return `${species.type}#${species.relation ?? ""}`;
}

/** Read the canonical subject from its declared resource field, never from its public id. */
function SubjectPicker({ species, disabled, onChange }: {
  species: DataResourceSubjectSpecies & { resource: string };
  disabled: boolean;
  onChange: (subject: string) => void;
}): React.ReactElement {
  const t = useUiT();
  const model = useModelMetadata(species.resource);
  const subjectField = model?.resource.subjectField;
  const info = React.useMemo(
    () => relationFieldInfoForResource(species.resource, model),
    [species.resource, model],
  );
  const [selectedId, setSelectedId] = React.useState("");
  const [opened, setOpened] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const [searchText] = useDebounce(search, 250);
  const { list, options, rows } = useRelationOptions(info, {
    enabled: opened && Boolean(subjectField),
    fields: subjectField ? [subjectField] : [],
    searchText,
  });
  const labelId = React.useId();
  return (
    <FieldRoot>
      <FieldLabel id={labelId} nativeLabel={false} render={<span />}>
        {t("access.recipient")}
      </FieldLabel>
      <RelationPicker
        aria-labelledby={labelId}
        value={selectedId}
        options={options}
        readOnly={disabled || !subjectField || !info}
        onOpenChange={(open) => {
          setSearch("");
          if (open) setOpened(true);
        }}
        onSearchChange={setSearch}
        searchState={{
          pending: list.fetching || search !== searchText,
          error: list.error,
          retry: list.refetch,
        }}
        onChange={(id) => {
          const row = rows.find((item) => rowPublicId(item) === id);
          const value = row && subjectField ? rowValueAtPath(row, subjectField) : null;
          setSelectedId(id);
          onChange(typeof value === "string" ? value : "");
        }}
      />
      <ErrorBanner description={!subjectField || !info ? t("access.unavailableSubject") : null} />
    </FieldRoot>
  );
}
