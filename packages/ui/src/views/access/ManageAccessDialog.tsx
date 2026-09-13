import * as React from "react";
import {
  modelLabelSegment,
  type DataResourceGrantableRelation,
  type DataResourceSubjectType,
} from "@angee/metadata";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { InlineEmpty } from "../../fragments/InlineEmpty";
import { useUiT } from "../../i18n";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { Button } from "../../ui/button";
import { FieldLabel, FieldRoot } from "../../ui/field";
import { Select } from "../../ui/select";
import { SubjectPicker } from "./SubjectPicker";
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
  const [selectedSubjectTypeKey, setSelectedSubjectTypeKey] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const relation = grantable.find((item) => item.relation === relationName) ?? grantable[0];
  const subjectTypes = relation?.subjects.filter(
    (item): item is DataResourceSubjectType & { resource: string } => Boolean(item.resource),
  ) ?? [];
  const selectedSubjectType = subjectTypes.find((item) => subjectTypeKey(item) === selectedSubjectTypeKey) ?? subjectTypes[0];
  const grantControlsUnavailable = !fetching && !error && grantable.length === 0;
  const subjectPickerUnavailable = !fetching && !error && Boolean(relation) && subjectTypes.length === 0;
  const relationLabelId = React.useId();
  const subjectTypeLabelId = React.useId();
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
    { field: "label", header: t("access.recipient") },
    { field: "relation", header: t("access.relation") },
    ...(targetIds.length > 1 ? [{ field: "targetId", header: t("access.record") }] : []),
  ], [t, targetIds.length]);

  return (
    <ControlBandProvider host={undefined}>
      <ErrorBanner description={error?.message ?? null} actions={error ? (
        <Button size="sm" onClick={onRetry}>{t("collection.retry")}</Button>
      ) : undefined} />
      {grantControlsUnavailable ? (
        <InlineEmpty label={t("access.noCommonPermission")} />
      ) : <div className="grid gap-3">
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
                onValueChange={(value) => { setRelationName(value ?? ""); setSelectedSubjectTypeKey(""); setSubject(""); }}
              />
            </FieldRoot>
            <FieldRoot>
              <FieldLabel id={subjectTypeLabelId} nativeLabel={false} render={<span />}>
                {t("access.recipientType")}
              </FieldLabel>
              <Select
                aria-labelledby={subjectTypeLabelId}
                value={selectedSubjectType ? subjectTypeKey(selectedSubjectType) : ""}
                disabled={pending || fetching || Boolean(error) || subjectTypes.length === 0}
                options={subjectTypes.map((item) => ({
                  value: subjectTypeKey(item),
                  label: `${modelLabelSegment(item.resource)}${item.relation ? ` (${item.relation})` : ""}`,
                }))}
                onValueChange={(value) => { setSelectedSubjectTypeKey(value ?? ""); setSubject(""); }}
              />
            </FieldRoot>
          </div>
          {selectedSubjectType ? <SubjectPicker
            key={`${relation?.relation}:${subjectTypeKey(selectedSubjectType)}`}
            resource={selectedSubjectType.resource}
            value={subject}
            aria-label={t("access.recipient")}
            readOnly={pending || fetching || Boolean(error)}
            onChange={setSubject}
          /> : null}
          {subjectPickerUnavailable ? <InlineEmpty label={t("access.unavailableSubject")} /> : null}
          <Button
            type="button" variant="primary" size="sm"
            disabled={pending || fetching || Boolean(error) || !subject || !relation || targetIds.length === 0}
            onClick={async () => {
              if (!relation || !subject) return;
              setPending(true);
              try {
                if (await onGrant(relation.relation, subject)) {
                  setSubject("");
                }
              } finally {
                setPending(false);
              }
            }}
          >{t("access.add")}</Button>
        </div>}
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

function subjectTypeKey(subjectType: DataResourceSubjectType): string {
  return `${subjectType.type}#${subjectType.relation ?? ""}`;
}
