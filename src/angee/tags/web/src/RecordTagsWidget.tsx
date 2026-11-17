import * as React from "react";
import { holdsPermission, rowPublicId, useModelMetadata, useResourceInvalidates, type Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  FieldRoot, FieldLabel, RelationMultiFieldWidget, relationFieldInfoForResource, relationIdList, relationValueId,
  useBusyRun, useRecordChromeContext, useRelationOptions, useRuntimeViewAs,
  type RelationCreateConfig, type WidgetDefinition, type WidgetRenderProps,
} from "@angee/ui";

import { TagRecord, UntagRecord } from "./documents";
import { useTagsT } from "./i18n";

const TAG_MODEL = "tags.Tag";
const CATEGORY_MODEL = "tags.TagCategory";
const CATEGORY_ORDER = [{ field: "position", order: "asc" as const }];

/** One picker's share of the record's tags: a category's, or the uncategorised ones (`id` empty). */
interface TagGroup {
  id: string;
  label: string;
  archived: boolean;
  /** Narrows the picker's options to the group; absent while no category exists. */
  where?: Record<string, unknown>;
}

/**
 * The record's tags as one picker per tag category, in category order, then one for
 * its uncategorised tags; with no categories, a single picker. Independent tag verbs
 * also serve records whose scalar fields are read-only. The server judges each
 * category's maximum, and its refusal reaches the error toast.
 */
function RecordTagsWidget({ readOnly }: WidgetRenderProps): React.ReactElement | null {
  const context = useRecordChromeContext();
  const t = useTagsT();
  const preview = useRuntimeViewAs();
  const metadata = useModelMetadata(context.resource);
  const relation = relationFieldInfoForResource(TAG_MODEL, useModelMetadata(TAG_MODEL));
  const categoryRelation = relationFieldInfoForResource(CATEGORY_MODEL, useModelMetadata(CATEGORY_MODEL));
  const models = [context.resource, "tags.TagAssignment"];
  const invalidates = useResourceInvalidates(models);
  const options = { invalidateModels: models, invalidates, errorNotification: false } as const;
  const [tag] = useAuthoredMutation(TagRecord, options);
  const [untag] = useAuthoredMutation(UntagRecord, options);
  const busy = useBusyRun();
  const running = React.useRef(false);
  const labelId = React.useId();
  const record: Row | null = context.record;
  const recordId = rowPublicId(record);
  const type = metadata?.resource.resourceType;
  const tags = Array.isArray(record?.tags) ? record.tags : [];
  const ids = relationIdList(tags);
  const categories = useRelationOptions(categoryRelation, { fields: ["is_archived"], sorters: CATEGORY_ORDER }).rows;
  // The record's own selection carries no category, so read where each held tag is filed.
  const filing = categories.length > 0 && ids.length > 0;
  const filed = useRelationOptions(relation, {
    enabled: filing,
    fields: ["category.id"],
    ...(filing ? { where: { id: { _in: ids } } } : {}),
  }).rows;
  const disabled = readOnly || context.actionsBlocked || !holdsPermission(record, "write") || preview.viewAs || preview.pending || busy.busy;
  if (!recordId || !type || !relation) return null;

  const filedUnder = new Map(filed.map((row): [string, string] => [rowPublicId(row) ?? "", relationValueId(row.category)]));
  const held = (group: TagGroup): readonly unknown[] =>
    tags.filter((value) => (filedUnder.get(relationValueId(value)) ?? "") === group.id);
  const labelField = categoryRelation?.labelField ?? "name";
  const groups: TagGroup[] = categories.length === 0
    ? [{ id: "", label: t("record.label"), archived: false }]
    : [
      ...categories.map((row) => {
        const id = rowPublicId(row) ?? "";
        return { id, label: String(row[labelField] ?? id), archived: row.is_archived === true, where: { category: { _eq: id } } };
      }),
      { id: "", label: t("record.other"), archived: false, where: { category: { _is_null: true } } },
    ];
  // An archived category stays visible only while the record still holds one of its tags.
  const shown = groups.filter((group) => !group.archived || held(group).length > 0);
  // A tag created from a category's picker is filed under that category.
  const create = (group: TagGroup): RelationCreateConfig | null | undefined =>
    !group.id ? undefined : relation.canCreate ? { resource: TAG_MODEL, defaultValues: { category: group.id } } : null;

  const change = async (group: TagGroup, next: readonly unknown[]): Promise<void> => {
    // One change at a time: a second edit computes its diff from the record the first one settles.
    if (disabled || running.current) return;
    running.current = true;
    await busy.settle(async () => {
      const wanted = relationIdList(next);
      const previous = relationIdList(held(group));
      const added = wanted.filter((id) => !previous.includes(id));
      const removed = previous.filter((id) => !wanted.includes(id));
      if (added.length) await tag({ type, id: recordId, tags: added });
      if (removed.length) await untag({ type, id: recordId, tags: removed });
    }, undefined, t("record.error")).finally(() => {
      running.current = false;
    });
  };
  return <>
    {shown.map((group) => (
      <FieldRoot key={group.id || "uncategorised"}>
        <FieldLabel id={`${labelId}-${group.id || "uncategorised"}`} nativeLabel={false} render={<span />}>
          {group.label}
        </FieldLabel>
        <RelationMultiFieldWidget relation={relation} value={held(group)} aria-label={group.label}
          {...(group.where ? { where: group.where } : {})} create={create(group)}
          readOnly={Boolean(disabled)} onChange={(next) => void change(group, next)} />
      </FieldRoot>
    ))}
  </>;
}

export const recordTagsWidget: WidgetDefinition = {
  edit: RecordTagsWidget,
  read: (props) => <RecordTagsWidget {...props} readOnly />,
};
