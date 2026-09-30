import * as React from "react";
import {
  modelLabelSegment,
  type DataResourceGrantableRelation,
  type DataResourceSubjectType,
} from "@angee/metadata";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { InlineEmpty } from "../../fragments/InlineEmpty";
import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { Button } from "../../ui/button";
import { FieldLabel, FieldRoot } from "../../ui/field";
import { Select } from "../../ui/select";
import { titleCase } from "../../lib/titleCase";
import { Skeleton } from "../../ui/skeleton";
import { SubjectPicker } from "./SubjectPicker";
import { defineRowAction } from "../resource/RowActions";
import { RowsListView } from "../resource/RowsListView";

/** Presentation contract; the contributing addon owns its typed API adapter. */
export type RecordAccessEntry = {
  id: string;
  targetId: string;
  relation: string;
  relationLabel?: string;
  subject: string;
  subjectType: string;
  subjectTypeLabel?: string;
  label: string;
};

/** An effective reader, enriched by the role owner where provenance is known. */
export interface AccessPerson {
  subject: string;
  label: string;
  relation?: string | null;
  roleId?: string;
  seatId?: string;
  roleLabel?: string;
  you?: boolean;
  following?: boolean;
  removable?: boolean;
}

/** A role owner's offered admission verb for one record. */
export interface AccessRole {
  id: string;
  label: string;
  subjectResource: string;
  offered: boolean;
}

/** A policy owner's offered values and their one-sentence consequence. */
export interface AccessVisibility {
  id: string;
  label: string;
  consequence: string;
  value: string;
  options?: readonly { value: string; label: string }[];
  offered?: readonly string[];
  actionLabel?: string;
  onSelect?: (value: string) => Promise<void>;
  /** Owner performs its declared confirmation before the one-way verb. */
  onAct?: () => Promise<void>;
}

export interface ManageAccessDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Omit for the Share button; null when opened by an existing action. */
  trigger?: React.ReactElement | null;
  label?: string;
  compact?: boolean;
  targetIds: readonly string[];
  grantable: readonly DataResourceGrantableRelation[];
  entries: readonly RecordAccessEntry[];
  fetching: boolean;
  error: Error | null;
  onRetry: () => void;
  onGrant: (relation: string, subject: string) => Promise<boolean>;
  onRevoke: (entry: RecordAccessEntry) => Promise<void>;
  /** One-record people surface; selection sharing retains the direct-grants view. */
  people?: readonly AccessPerson[];
  /** The one-record reader read has completed, so its count is known. */
  peopleLoaded?: boolean;
  roles?: readonly AccessRole[];
  visibility?: readonly AccessVisibility[];
  directShare?: boolean;
  onAddRole?: (role: string, subject: string) => Promise<boolean>;
  onRemovePerson?: (person: AccessPerson) => Promise<void>;
}

/** Direct access for one record or a selection, over the shared collection owners. */
export function ManageAccessDialog(props: ManageAccessDialogProps): React.ReactElement {
  const t = useUiT();
  const label = props.label ?? t("access.selection", { count: props.targetIds.length });
  const trigger = props.trigger === undefined ? (
    <Button
      type="button"
      variant={props.compact || props.people?.length ? "ghost" : "icon"}
      size={props.compact || props.people?.length ? "sm" : "iconMd"}
      aria-label={t(props.compact ? "access.people" : "access.share")}
      disabled={props.targetIds.length === 0}
    >
      <Glyph name="share" />
      {props.compact ? <span>{t("access.people")}{props.peopleLoaded ? ` · ${props.people?.length ?? 0}` : null}</span> : null}
      {props.people && props.people.length > 0 ? <span aria-hidden="true" className="ml-1 inline-flex -space-x-1">
        {props.people.slice(0, 3).map((person) => <span key={person.subject}
          className="inline-flex size-5 items-center justify-center rounded-full border border-surface bg-surface-raised text-[10px] font-semibold">
          {person.label.charAt(0).toUpperCase()}
        </span>)}
      </span> : null}
    </Button>
  ) : props.trigger;
  return (
    <DialogForm
      open={props.open}
      onOpenChange={props.onOpenChange}
      {...(trigger ? { trigger } : {})}
      title={t("access.title", { label })}
      description={props.targetIds.length > 1 ? t("access.directOnly") : undefined}
      size="lg"
    >
      {props.open ? <AccessContents {...props} /> : null}
    </DialogForm>
  );
}

function AccessContents({
  targetIds, grantable, entries, fetching, error, onRetry, onGrant, onRevoke,
  people, roles, visibility, directShare, onAddRole, onRemovePerson,
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
    { field: "relationLabel", header: t("access.relation") },
    ...(targetIds.length > 1 ? [{ field: "targetId", header: t("access.record") }] : []),
  ], [t, targetIds.length]);

  if (targetIds.length === 1 && people !== undefined) {
    return <ControlBandProvider host={undefined}><PeopleContents
      people={people} roles={roles ?? []} visibility={visibility ?? []}
      grantable={directShare === false ? [] : grantable}
      entries={entries} fetching={fetching} error={error} onRetry={onRetry}
      onGrant={onGrant} onRevoke={onRevoke} onAddRole={onAddRole}
      onRemovePerson={onRemovePerson}
    /></ControlBandProvider>;
  }

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
                options={grantable.map((item) => ({ value: item.relation, label: item.label ?? item.relation.toLowerCase() }))}
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
                  label: `${titleCase(modelLabelSegment(item.resource))}${item.relation ? ` (${titleCase(item.relation)})` : ""}`,
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

function PeopleContents({
  people, roles, visibility, grantable, entries, fetching, error, onRetry,
  onGrant, onRevoke, onAddRole, onRemovePerson,
}: Required<Pick<ManageAccessDialogProps, "people" | "roles" | "visibility" | "grantable" | "entries" | "fetching" | "error" | "onRetry" | "onGrant" | "onRevoke">> &
  Pick<ManageAccessDialogProps, "onAddRole" | "onRemovePerson">): React.ReactElement {
  const t = useUiT();
  const [selected, setSelected] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const directActions = React.useMemo(() => [defineRowAction<RecordAccessEntry>({
    kind: "page", id: "revoke", label: t("access.remove"), icon: "x", variant: "ghost",
    pendingPolicy: "disable-actions", disabled: () => pending || fetching,
    onSelect: async (entry) => {
      setPending(true);
      try { await onRevoke(entry); } finally { setPending(false); }
    },
  })], [fetching, onRevoke, pending, t]);
  const choices = [
    ...roles.filter((role) => role.offered && Boolean(onAddRole)).map((role) => ({
      id: `role:${role.id}`, kind: "role" as const, relation: role.id,
      label: role.label, resource: role.subjectResource,
    })),
    ...grantable.flatMap((relation) => relation.subjects.flatMap((subjectType) => subjectType.resource ? [{
      id: `direct:${relation.relation}:${subjectTypeKey(subjectType)}`, kind: "direct" as const,
      relation: relation.relation,
      label: `${t("access.direct")} · ${relation.label ?? relation.relation.toLowerCase()} · ${titleCase(modelLabelSegment(subjectType.resource))}`,
      resource: subjectType.resource,
    }] : [])),
  ];
  const choice = choices.find((item) => item.id === selected) ?? choices[0];

  return <div className="grid gap-5">
    <ErrorBanner description={error?.message ?? null} actions={error ? (
      <Button size="sm" onClick={onRetry}>{t("collection.retry")}</Button>
    ) : undefined} />
    <section className="grid gap-2" aria-label={t("access.people")}>
      <div className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{t("access.people")} · {people.length}</div>
      {fetching && people.length === 0 ? <div className="grid gap-2" aria-label={t("access.people")}>
        <Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" />
      </div> : people.length === 0 ? <InlineEmpty label={t("access.noReaders")} /> : people.map((person) => {
        return <div key={person.subject} className="flex items-center gap-3 border-b border-border-subtle py-2 last:border-b-0">
          <span aria-hidden="true" className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-surface-raised font-semibold">
            {person.label.charAt(0).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{person.label}{person.you ? <span className="ml-1 text-xs text-fg-muted">{t("access.you")}</span> : null}</div>
            {person.roleLabel ? <div className="text-xs text-fg-muted">{person.roleLabel}</div> : null}
          </div>
          {person.following ? <span title={t("access.following")} aria-label={t("access.following")}><Glyph name="bell" /></span> : null}
          {person.removable && onRemovePerson ? <Button type="button" size="sm" variant="ghost"
            disabled={pending || fetching} onClick={async () => {
              setPending(true);
              try { await onRemovePerson(person); }
              finally { setPending(false); }
            }}>{t("access.remove")}</Button> : null}
        </div>;
      })}
    </section>
    {entries.length > 0 ? <section aria-label={t("access.direct")} className="grid gap-2">
      <div className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{t("access.direct")}</div>
      <RowsListView scope="local" presentation="embedded" rows={entries} fetching={fetching}
        columns={[{ field: "label", header: t("access.recipient") },
          { field: "subjectTypeLabel", header: t("access.recipientType") },
          { field: "relationLabel", header: t("access.relation") }]}
        rowActions={directActions} emptyContent={t("access.empty")} />
    </section> : null}
    {choices.length > 0 ? <section className="grid gap-3" aria-label={t("access.addPerson")}>
      <div className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{t("access.addPerson")}</div>
      {choices.length > 1 ? <Select aria-label={t("access.relation")} value={choice?.id ?? ""}
        disabled={pending || fetching} options={choices.map(({ id, label }) => ({ value: id, label }))}
        onValueChange={(value) => { setSelected(value ?? ""); setSubject(""); }} /> : null}
      {choice?.resource ? <SubjectPicker key={choice.id} resource={choice.resource} value={subject}
        aria-label={t("access.recipient")} readOnly={pending || fetching} onChange={setSubject} /> : null}
      <Button type="button" size="sm" variant="primary" disabled={!subject || !choice?.resource || pending || fetching}
        onClick={async () => {
          if (!subject || !choice) return;
          setPending(true);
          try {
            const ok = choice.kind === "direct"
              ? await onGrant(choice.relation, subject)
              : await onAddRole?.(choice.relation, subject);
            if (ok) setSubject("");
          } finally { setPending(false); }
        }}>{t("access.add")}</Button>
    </section> : null}
    {visibility.length > 0 ? <section className="grid gap-3" aria-label={t("access.visibility")}>
      <div className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{t("access.visibility")}</div>
      {visibility.map((policy) => <div key={policy.id} className="grid gap-1">
        <div className="flex items-center justify-between gap-3"><span className="text-sm font-medium">{policy.label}</span>
          {policy.onAct && policy.actionLabel ? <Button type="button" variant="secondary" size="sm" disabled={pending}
            onClick={async () => { setPending(true); try { await policy.onAct?.(); } finally { setPending(false); } }}>
            {policy.actionLabel}</Button> : policy.options && policy.onSelect ? <Select aria-label={policy.label}
            value={policy.value} disabled={pending}
            options={policy.options.filter((option) => policy.offered?.includes(option.value) ?? true)}
            onValueChange={async (value) => { if (value) await policy.onSelect?.(value); }} />
            : <span className="text-sm">{policy.options?.find((option) => option.value === policy.value)?.label ?? policy.value}</span>}
        </div>
        <p className="text-xs text-fg-muted">{policy.consequence}</p>
      </div>)}
    </section> : null}
  </div>;
}

function subjectTypeKey(subjectType: DataResourceSubjectType): string {
  return `${subjectType.resource ?? ""}:${subjectType.type}#${subjectType.relation ?? ""}`;
}
