import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
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
  useActionResultRun,
  useRecordChromeContext,
  useResourceViewUtilityContext,
  type ManageAccessDialogProps,
  type RecordAccessEntry,
} from "@angee/ui";

import { RecordAccessDocument } from "./documents";

/** IAM contributes the adapter once; models declare their share surface. */
export function ShareRecordChrome(): React.ReactElement {
  const record = useRecordChromeContext();
  return <ShareAccess
    resource={record.resource}
    targetIds={[record.recordId]}
    record={record.record}
  />;
}

export function ShareListChrome(): React.ReactElement {
  const list = useResourceViewUtilityContext();
  return <ShareAccess
    resource={list.resource}
    targetIds={[...(list.selectedIds ?? [])].sort()}
  />;
}

export interface ShareAccessDialogProps extends Pick<ManageAccessDialogProps, "open" | "onOpenChange" | "trigger"> {
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
  if (!model?.resource.resourceType || !model.resource.grantable?.length) return null;
  return <BoundShareAccess
    key={`${accessResource}:${JSON.stringify(targetIds)}`}
    resource={model.resource}
    targetIds={targetIds}
    {...props}
  />;
}

function BoundShareAccess({ resource, targetIds, record, label: suppliedLabel, open, onOpenChange, trigger }: Omit<ShareAccessDialogProps, "resource"> & {
  resource: DataResourceMetadata;
}): React.ReactElement {
  const stableTargetIds = useStableArray(targetIds);
  const invalidates = useResourceInvalidates([resource.modelLabel]);
  const query = useAuthoredQuery(RecordAccessDocument, {
    targetType: resource.resourceType ?? "",
    targetIds: [...stableTargetIds],
  }, { enabled: open && stableTargetIds.length > 0, dataProviderName: "console", models: [resource.modelLabel] });
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
      subject: entry.subject,
      subjectType: entry.subject_type,
      label: entry.label,
    })),
    [query.data?.record_access],
  );
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
  return <ManageAccessDialog
    open={open}
    onOpenChange={onOpenChange}
    {...(trigger === undefined ? {} : { trigger })}
    {...(label === undefined ? {} : { label })}
    targetIds={stableTargetIds}
    grantable={availableRelations}
    entries={entries}
    fetching={query.isFetching}
    error={query.error}
    onRetry={retry}
    onGrant={grantAccess}
    onRevoke={revokeAccess}
  />;
}
