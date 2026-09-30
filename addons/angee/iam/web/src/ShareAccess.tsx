import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  holdsPermission,
  modelLabelSegment,
  rowValueAtPath,
  useModelMetadata,
  useResourceInvalidates,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";
import { useActionMutation, useAuthoredQuery, useStableArray } from "@angee/refine";
import {
  ManageAccessDialog,
  FormView,
  useSlot,
  useActionResultRun,
  useRecordChromeContext,
  useModelSlot,
  useResourceViewUtilityContext,
  useUiT,
  type ManageAccessDialogProps,
  type RecordAccessEntry,
  titleCase,
} from "@angee/ui";

import { RecordAccessDocument, RecordReadersDocument } from "./documents";

type AccessPerson = NonNullable<ManageAccessDialogProps["people"]>[number];
type AccessRole = NonNullable<ManageAccessDialogProps["roles"]>[number];
type AccessVisibility = NonNullable<ManageAccessDialogProps["visibility"]>[number];

/** An owner's role adapter contributes its list and verbs to one open panel. */
export interface AccessRoleState {
  role: AccessRole;
  people: readonly AccessPerson[];
  add: (subject: string) => Promise<boolean>;
  remove: (person: AccessPerson) => Promise<void>;
}

export interface AccessRoleOwnerProps {
  targetId: string;
  record: Row | null | undefined;
}

const AccessRoleRegistration = React.createContext<((id: string, value: AccessRoleState | null) => void) | null>(null);

/** Register one model-owned role while the IAM panel is open. */
export function useAccessRole(id: string, state: AccessRoleState): void {
  const register = React.useContext(AccessRoleRegistration);
  React.useEffect(() => {
    register?.(id, state);
    return () => register?.(id, null);
  }, [id, register, state]);
}

const AccessVisibilityRegistration = React.createContext<((id: string, value: AccessVisibility | null) => void) | null>(null);

/** Register one model-owned visibility policy while the IAM panel is open. */
export function useAccessVisibility(id: string, state: AccessVisibility): void {
  const register = React.useContext(AccessVisibilityRegistration);
  React.useEffect(() => {
    register?.(id, state);
    return () => register?.(id, null);
  }, [id, register, state]);
}

function mayShowShare(record: Row | null | undefined, resource: DataResourceMetadata | undefined,
  contributions: readonly unknown[] = []): boolean {
  if (!record || !("permissions" in record)) return true;
  return (resource?.grantable ?? []).some((relation) => holdsPermission(record, relation.permission))
    || contributions.length > 0;
}

/** IAM contributes the adapter once; models declare their share surface. */
export function ShareRecordChrome(): React.ReactElement | null {
  const record = useRecordChromeContext();
  const listed = useModelMetadata(record.resource);
  const canonical = useModelMetadata(listed?.resource.canonicalLabel ?? record.resource);
  const model = listed?.resource.grantable?.length ? listed : canonical;
  const roles = useModelSlot({ slot: "access.roles", model: model?.resource.modelLabel ?? record.resource });
  const visibility = useModelSlot({ slot: "access.visibility", model: model?.resource.modelLabel ?? record.resource });
  if (!mayShowShare(record.record, model?.resource, [...roles, ...visibility])) return null;
  return <ShareAccess
    resource={record.resource}
    targetIds={[record.recordId]}
    record={record.record}
  />;
}

/** The same Share panel presented as a compact record-rail entry. */
export function ShareAccessCompact(): React.ReactElement | null {
  const record = useRecordChromeContext();
  const listed = useModelMetadata(record.resource);
  const canonical = useModelMetadata(listed?.resource.canonicalLabel ?? record.resource);
  const model = listed?.resource.grantable?.length ? listed : canonical;
  const roles = useModelSlot({ slot: "access.roles", model: model?.resource.modelLabel ?? record.resource });
  const visibility = useModelSlot({ slot: "access.visibility", model: model?.resource.modelLabel ?? record.resource });
  if (!mayShowShare(record.record, model?.resource, [...roles, ...visibility])) return null;
  return <ShareAccess resource={record.resource} targetIds={[record.recordId]}
    record={record.record} compact />;
}

function PeopleRailLabel(): React.ReactElement {
  const t = useUiT();
  return <>{t("access.people")}</>;
}

/** Model owners place this shared compact People group in their rail slot. */
export const ShareAccessRailGroup = <FormView.RailGroup id="people" label={<PeopleRailLabel />}
  content={<ShareAccessCompact />} />;

export function ShareListChrome(): React.ReactElement {
  const list = useResourceViewUtilityContext();
  return <ShareAccess
    resource={list.resource}
    targetIds={[...(list.selectedIds ?? [])].sort()}
  />;
}

export interface ShareAccessDialogProps extends Pick<ManageAccessDialogProps, "open" | "onOpenChange" | "trigger" | "compact"> {
  resource: string;
  targetIds: readonly string[];
  record?: Row | null;
  label?: string;
}

function ShareAccess(props: Omit<ShareAccessDialogProps, "open" | "onOpenChange">): React.ReactElement | null {
  const [open, setOpen] = React.useState(false);
  return <ShareAccessDialog {...props} open={open} onOpenChange={setOpen} />;
}

/** Open the canonical IAM access surface from a record action or setup task. */
export function ShareAccessDialog({ resource, targetIds, ...props }: ShareAccessDialogProps): React.ReactElement | null {
  const listedModel = useModelMetadata(resource);
  const accessResource = listedModel?.resource.grantable?.length
    ? resource
    : listedModel?.resource.canonicalLabel ?? resource;
  const model = useModelMetadata(accessResource);
  const roleEntries = useModelSlot({ slot: "access.roles", model: accessResource });
  const visibilityEntries = useModelSlot({ slot: "access.visibility", model: accessResource });
  if (!model?.resource.resourceType ||
    (!model.resource.grantable?.length && !roleEntries.length && !visibilityEntries.length)) return null;
  return <BoundShareAccess
    key={`${accessResource}:${JSON.stringify(targetIds)}`}
    resource={model.resource}
    targetIds={targetIds}
    roleEntries={roleEntries}
    visibilityEntries={visibilityEntries}
    {...props}
  />;
}

function BoundShareAccess({ resource, targetIds, record, label: suppliedLabel, open, onOpenChange, trigger, compact, roleEntries, visibilityEntries }: Omit<ShareAccessDialogProps, "resource"> & {
  resource: DataResourceMetadata;
  roleEntries: ReturnType<typeof useModelSlot>;
  visibilityEntries: ReturnType<typeof useModelSlot>;
}): React.ReactElement {
  const t = useUiT();
  const [roleStates, setRoleStates] = React.useState<Record<string, AccessRoleState>>({});
  const registerRole = React.useCallback((id: string, value: AccessRoleState | null) => {
    setRoleStates((current) => {
      if (value === null) {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      }
      return current[id] === value ? current : { ...current, [id]: value };
    });
  }, []);
  const [visibilityStates, setVisibilityStates] = React.useState<Record<string, AccessVisibility>>({});
  const registerVisibility = React.useCallback((id: string, value: AccessVisibility | null) => {
    setVisibilityStates((current) => {
      if (value === null) {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      }
      return current[id] === value ? current : { ...current, [id]: value };
    });
  }, []);
  const roles = roleEntries.flatMap((entry) => roleStates[entry.id]?.role ?? []);
  const visibility = visibilityEntries.flatMap((entry) => visibilityStates[entry.id] ?? []);
  const rolePeople = roleEntries.flatMap((entry) => roleStates[entry.id]?.people ?? []);
  const onAddRole = React.useCallback((id: string, subject: string) =>
    roleStates[id]?.add(subject) ?? Promise.resolve(false), [roleStates]);
  const onRemovePerson = React.useCallback((person: AccessPerson) =>
    person.roleId ? roleStates[person.roleId]?.remove(person) ?? Promise.resolve() : Promise.resolve(), [roleStates]);
  const directShare = useSlot("access.direct").some((entry) => entry.id === "iam.direct");
  const stableTargetIds = useStableArray(targetIds);
  const invalidates = useResourceInvalidates([resource.modelLabel]);
  const query = useAuthoredQuery(RecordAccessDocument, {
    targetType: resource.resourceType ?? "",
    targetIds: [...stableTargetIds],
  }, { enabled: open && stableTargetIds.length > 0 && Boolean(resource.grantable?.length), dataProviderName: "console", models: [resource.modelLabel] });
  const readers = useAuthoredQuery(RecordReadersDocument, {
    targetType: resource.resourceType ?? "",
    targetId: stableTargetIds[0] ?? "",
  }, { enabled: stableTargetIds.length === 1,
    dataProviderName: "console", models: [resource.modelLabel] });
  const options = React.useMemo(() => ({
    idArgument: null,
    dataProviderName: "console",
    invalidateModels: [resource.modelLabel],
    invalidates,
  }), [invalidates, resource.modelLabel]);
  const [grant] = useActionMutation<ActionFieldName>("grant_record_access", options);
  const [revoke] = useActionMutation<ActionFieldName>("revoke_record_access", options);
  const run = useActionResultRun();
  const retry = React.useCallback(() => { void query.refetch(); }, [query.refetch]);
  const grantAccess = React.useCallback(async (relation: string, subject: string) => {
    const result = await run(() => grant("", {
      target_type: resource.resourceType,
      target_ids: stableTargetIds,
      relation,
      subject,
    }));
    return result?.ok ?? false;
  }, [grant, resource.resourceType, run, stableTargetIds]);
  const revokeAccess = React.useCallback(async (entry: RecordAccessEntry) => {
    await run(() => revoke("", {
      target_type: resource.resourceType,
      target_ids: [entry.targetId],
      relation: entry.relation,
      subject: entry.subject,
    }));
  }, [resource.resourceType, revoke, run]);
  const entries = React.useMemo<readonly RecordAccessEntry[]>(
    () => (query.data?.record_access ?? []).map((entry) => ({
      id: JSON.stringify([entry.target_id, entry.relation, entry.subject]),
      targetId: entry.target_id,
      relation: entry.relation,
      relationLabel: resource.grantable?.find((relation) => relation.relation === entry.relation)?.label ?? entry.relation.toLowerCase(),
      subject: entry.subject,
      subjectType: entry.subject_type,
      subjectTypeLabel: titleCase(modelLabelSegment((resource.grantable ?? []).flatMap((relation) => relation.subjects)
        .find((subject) => subject.type === entry.subject_type)?.resource
        ?? entry.subject_type)),
      label: entry.label,
    })),
    [query.data?.record_access, resource.grantable],
  );
  const people = React.useMemo<readonly AccessPerson[] | undefined>(() => {
    if (stableTargetIds.length !== 1) return undefined;
    const roleBySubject = new Map(rolePeople.map((person) => [person.subject, person]));
    const directBySubject = new Map(entries.map((entry) => [entry.subject, entry]));
    return (readers.data?.record_readers ?? []).map((reader) => {
      const role = roleBySubject.get(reader.subject);
      const direct = directBySubject.get(reader.subject);
      return {
        subject: reader.subject, label: reader.label,
        relation: direct?.relation,
        roleLabel: role?.roleLabel ?? (direct ? t("access.direct") : undefined),
        you: reader.you, following: reader.following,
        removable: role?.removable ?? false,
        roleId: role?.roleId, seatId: role?.seatId,
      };
    });
  }, [entries, readers.data?.record_readers, rolePeople, stableTargetIds.length, t]);
  const availableRelations = React.useMemo(() => {
    const allowed = new Map(
      (query.data?.record_access_options ?? []).map((option) => [option.relation, option.permission]),
    );
    return (resource.grantable ?? []).filter(
      (relation) => allowed.get(relation.relation) === relation.permission,
    );
  }, [query.data?.record_access_options, resource.grantable]);
  const representation = record && resource.recordRepresentation
    ? rowValueAtPath(record, resource.recordRepresentation) : null;
  const label = suppliedLabel ?? (typeof representation === "string" && representation
    ? representation
    : stableTargetIds.length === 1
      ? modelLabelSegment(resource.modelLabel)
      : undefined);
  return <AccessRoleRegistration.Provider value={registerRole}><AccessVisibilityRegistration.Provider value={registerVisibility}>
    {open && stableTargetIds.length === 1 ? roleEntries.map((entry) =>
      <React.Fragment key={entry.id}>{React.createElement(entry.content as React.ComponentType<AccessRoleOwnerProps>, {
        targetId: stableTargetIds[0]!, record,
      })}</React.Fragment>) : null}
    {open && stableTargetIds.length === 1 ? visibilityEntries.map((entry) =>
      <React.Fragment key={entry.id}>{React.createElement(entry.content as React.ComponentType<AccessRoleOwnerProps>, {
        targetId: stableTargetIds[0]!, record,
      })}</React.Fragment>) : null}
    <ManageAccessDialog
    open={open}
    compact={compact}
    onOpenChange={onOpenChange}
    {...(trigger === undefined ? {} : { trigger })}
    {...(label === undefined ? {} : { label })}
    targetIds={stableTargetIds}
    grantable={directShare ? availableRelations : []}
    entries={entries}
    people={people}
    peopleLoaded={readers.data?.record_readers !== undefined}
    roles={roles}
    visibility={visibility}
    directShare={directShare}
    onAddRole={onAddRole}
    onRemovePerson={onRemovePerson}
    fetching={query.isFetching || readers.isFetching}
    error={query.error ?? readers.error}
    onRetry={() => { retry(); void readers.refetch(); }}
    onGrant={grantAccess}
    onRevoke={revokeAccess}
    />
  </AccessVisibilityRegistration.Provider></AccessRoleRegistration.Provider>;
}
