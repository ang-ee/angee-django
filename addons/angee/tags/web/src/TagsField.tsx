import { useModelMetadata } from "@angee/metadata";
import {
  InlineEmpty,
  RelationMultiFieldWidget,
  relationFieldInfoForResource,
  relationIdList,
  useAuthoredResourceMutation,
  useRecordChromeContextMaybe,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";
import * as React from "react";

import { TagDocument, UntagDocument } from "./documents.console";
import { useTagsT } from "./i18n";

const TAG_MODEL = "tags.Tag";

/**
 * The `tags` field widget: the open record's tags as the standard relation
 * multi-select over `tags.Tag` (removable chips, picker, inline "New tag"),
 * writing each pick and removal at once through the `tag` / `untag` edge
 * verbs rather than the form's save. Resource metadata routes a placed
 * `<Field name="tags" />` here (the backend declares the widget key on the
 * field), so an owner names nothing. A read-only form, or a reader without
 * write on the record, gets linked chips; a create form has no record to tag
 * yet and says so.
 */
export function TagsField({ value, field, readOnly, controlRef }: WidgetRenderProps<readonly unknown[]>): React.ReactElement {
  const t = useTagsT();
  const chrome = useRecordChromeContextMaybe();
  const relation = relationFieldInfoForResource(TAG_MODEL, useModelMetadata(TAG_MODEL));
  const resource = chrome?.resource;
  const targetType = useModelMetadata(resource ?? "")?.resource.resourceType;
  const invalidateModels = React.useMemo(() => (resource ? [resource] : []), [resource]);
  const [tag] = useAuthoredResourceMutation(TagDocument, { invalidateModels });
  const [untag] = useAuthoredResourceMutation(UntagDocument, { invalidateModels });
  // The picked set shown until the record's refreshed `tags` read replaces it.
  const [draft, setDraft] = React.useState<readonly string[] | null>(null);
  if (!chrome?.record || !targetType) {
    return <InlineEmpty icon="tag" label={t("field.unsaved")} />;
  }
  if (!relation) throw new Error(`${TAG_MODEL} exposes no list root for the tags picker.`);
  const targetId = chrome.recordId;
  const change = async (next: readonly unknown[]): Promise<void> => {
    const before = draft ?? relationIdList(value);
    const after = relationIdList(next);
    const added = after.filter((id) => !before.includes(id));
    const removed = before.filter((id) => !after.includes(id));
    if (added.length === 0 && removed.length === 0) return;
    setDraft(after);
    try {
      if (added.length > 0) await tag({ targetType, targetId, tagIds: added });
      if (removed.length > 0) await untag({ targetType, targetId, tagIds: removed });
    } finally {
      setDraft(null);
    }
  };
  return (
    <RelationMultiFieldWidget
      value={draft ?? value ?? []}
      relation={relation}
      readOnly={readOnly || chrome.formReadOnly}
      controlRef={controlRef}
      controlProps={field?.controlProps}
      aria-label={typeof field?.label === "string" ? field.label : t("field.label")}
      onChange={(next) => void change(next)}
    />
  );
}

/** Registered as `angee.tags.tags`; list cells keep the shared relation-list chips. */
export const tagsWidget = { read: TagsField, edit: TagsField } satisfies WidgetDefinition<readonly unknown[]>;
