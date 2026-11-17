import * as React from "react";
import { holdsPermission, rowPublicId, useModelMetadata, useResourceInvalidates, type Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  FieldDescription, FieldRoot, FieldLabel, RelationMultiFieldWidget, SectionEyebrow, relationFieldInfoForResource,
  relationIdList,
  relationValueId, useBusyRun, useRecordChromeContext, useRelationOptions, useRuntimeViewAs,
  type RelationCreateConfig, type WidgetDefinition, type WidgetRenderProps,
} from "@angee/ui";

import { TagRecord, UntagRecord } from "./documents";
import { useTagsT } from "./i18n";

const TAG_MODEL = "tags.Tag";
const CATEGORY_MODEL = "tags.TagCategory";
const CATEGORY_FIELDS = ["position", "max_per_record", "is_archived"];
const CATEGORY_ORDER = [{ field: "position", order: "asc" as const }];
const FILING_FIELDS = ["category.id", "category.name", ...CATEGORY_FIELDS.map((field) => `category.${field}`)];

/** A tag category as the widget groups by it; the uncategorised group has an empty `id`. */
interface Category {
  id: string;
  name: string;
  position: number;
  maximum: number | null;
  archived: boolean;
}

function categoryFromRow(row: Row | null | undefined): Category | null {
  const id = relationValueId(row);
  if (!row || !id) return null;
  const position = Number(row.position);
  const maximum = Number(row.max_per_record);
  return {
    id,
    name: String(row.name ?? id),
    position: Number.isFinite(position) ? position : 0,
    maximum: row.max_per_record == null || !Number.isFinite(maximum) ? null : maximum,
    archived: row.is_archived === true,
  };
}

/**
 * The record's tags, grouped: one picker per tag category in category order, then
 * one for the uncategorised tags; with no categories, a single picker. Independent
 * tag verbs also serve records whose scalar fields are read-only.
 *
 * Each held tag's category is read once and kept, so a refetch never regroups the
 * pickers, and a tag picked from a group joins it at once. A held tag's category
 * gets its picker even when the category list stops short of it. Each picker's one
 * name is its visible label, which the field wires as `aria-labelledby`; a category's
 * maximum is its description (`aria-describedby`). At a maximum of one, a new pick
 * replaces the held tag, untagging
 * before tagging. Otherwise the server judges the maximum and its refusal reaches
 * the error toast.
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
  const categoryList = useRelationOptions(categoryRelation, { fields: CATEGORY_FIELDS, sorters: CATEGORY_ORDER });
  // The record's own selection carries no category, so read where each held tag is filed.
  const [filedUnder, setFiledUnder] = React.useState<ReadonlyMap<string, Category | null>>(new Map());
  const unfiled = ids.filter((id) => !filedUnder.has(id));
  const filing = categoryList.rows.length > 0 && unfiled.length > 0;
  const filedQuery = useRelationOptions(relation, {
    enabled: filing,
    fields: FILING_FIELDS,
    ...(filing ? { where: { id: { _in: unfiled } } } : {}),
  });
  const fresh = filedQuery.rows.map((row): [string, Category | null] => [
    relationValueId(row), categoryFromRow(row.category as Row | null),
  ]);
  // Rows read in this render group at once; the effect keeps them once the query is disabled.
  const known: ReadonlyMap<string, Category | null> = fresh.length ? new Map([...filedUnder, ...fresh]) : filedUnder;
  React.useEffect(() => {
    if (!filedQuery.rows.length) return;
    setFiledUnder((previous) => new Map([
      ...previous,
      ...filedQuery.rows.map((row): [string, Category | null] => [
        relationValueId(row), categoryFromRow(row.category as Row | null),
      ]),
    ]));
  }, [filedQuery.rows]);
  const disabled = readOnly || context.actionsBlocked || !holdsPermission(record, "write") || preview.viewAs || preview.pending || busy.busy;
  // Place pickers once the reads settle, so they never regroup; a failed read leaves its tags uncategorised.
  const settled = !categoryList.list.fetching && !(filing && filedQuery.list.fetching);
  if (!recordId || !type || !relation) return null;

  const categories = new Map(categoryList.rows.flatMap((row) => {
    const category = categoryFromRow(row);
    return category ? [[category.id, category] as const] : [];
  }));
  for (const category of known.values()) if (category && !categories.has(category.id)) categories.set(category.id, category);
  const held = (group: string): readonly unknown[] =>
    tags.filter((value) => (known.get(relationValueId(value))?.id ?? "") === group);
  const groups = [...categories.values()]
    .filter((category) => !category.archived || held(category.id).length > 0)
    .sort((left, right) => left.position - right.position || left.name.localeCompare(right.name));

  const change = async (group: Category | null, next: readonly unknown[]): Promise<void> => {
    // One change at a time: a second edit computes its diff from the record the first one settles.
    if (disabled || running.current) return;
    running.current = true;
    const groupId = group?.id ?? "";
    await busy.settle(async () => {
      const previous = relationIdList(held(groupId));
      let wanted = relationIdList(next);
      // At a maximum of one, a new pick replaces the held tag.
      if (group?.maximum === 1 && wanted.length > 1) wanted = wanted.filter((id) => !previous.includes(id)).slice(-1);
      const added = wanted.filter((id) => !previous.includes(id));
      const removed = previous.filter((id) => !wanted.includes(id));
      if (removed.length) await untag({ type, id: recordId, tags: removed });
      if (added.length) {
        await tag({ type, id: recordId, tags: added });
        setFiledUnder((previous) => new Map([...previous, ...added.map((id): [string, Category | null] => [id, group])]));
      }
    }, undefined, t("record.error")).finally(() => {
      running.current = false;
    });
  };
  // A tag created from a category's picker is filed under that category.
  const create = (group: Category | null): RelationCreateConfig | null | undefined =>
    !group ? undefined : relation.canCreate ? { resource: TAG_MODEL, defaultValues: { category: group.id } } : null;
  const picker = (group: Category | null, label: string): React.ReactElement => (
    <FieldRoot key={group?.id ?? "uncategorised"}>
      <FieldLabel nativeLabel={false} render={<span />}>{label}</FieldLabel>
      {/* The picker always carries an aria-label; it repeats the field's label so no second name differs. */}
      <RelationMultiFieldWidget relation={relation} value={held(group?.id ?? "")} aria-label={label}
        {...(groups.length ? { where: { category: group ? { _eq: group.id } : { _is_null: true } } } : {})}
        create={create(group)} readOnly={Boolean(disabled)} onChange={(next) => void change(group, next)} />
      {group?.maximum != null ? <FieldDescription>{t("record.maximum", { count: group.maximum })}</FieldDescription> : null}
    </FieldRoot>
  );
  if (!groups.length) return settled ? picker(null, t("record.label")) : null;
  return (
    <div role="group" aria-labelledby={labelId} className="flex flex-col gap-3">
      <SectionEyebrow as="span" id={labelId}>{t("record.label")}</SectionEyebrow>
      {settled || known.size ? <>
        {groups.map((group) => picker(group, t("record.categoryLabel", { name: group.name })))}
        {picker(null, t("record.other"))}
      </> : null}
    </div>
  );
}

export const recordTagsWidget: WidgetDefinition = {
  edit: RecordTagsWidget,
  read: (props) => <RecordTagsWidget {...props} readOnly />,
};
