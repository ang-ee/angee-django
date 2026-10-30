import * as React from "react";
import { holdsPermission, rowPublicId, useModelMetadata, useResourceInvalidates, type Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  FieldRoot, FieldLabel, RelationMultiFieldWidget, relationFieldInfoForResource, relationIdList,
  useRecordChromeContext, useRuntimeViewAs, useToast, errorMessage, type WidgetDefinition, type WidgetRenderProps,
} from "@angee/ui";

import { TagRecord, UntagRecord } from "./documents";
import { useTagsT } from "./i18n";

/** Independent tag verbs also serve records whose scalar fields are read-only. */
function RecordTagsWidget({ readOnly }: WidgetRenderProps): React.ReactElement | null {
  const context = useRecordChromeContext();
  const t = useTagsT();
  const toast = useToast();
  const preview = useRuntimeViewAs();
  const metadata = useModelMetadata(context.resource);
  const relation = relationFieldInfoForResource("tags.Tag", useModelMetadata("tags.Tag"));
  const models = [context.resource, "tags.TagAssignment"];
  const invalidates = useResourceInvalidates(models);
  const options = { invalidateModels: models, invalidates, errorNotification: false } as const;
  const [tag] = useAuthoredMutation(TagRecord, options);
  const [untag] = useAuthoredMutation(UntagRecord, options);
  const [pending, setPending] = React.useState(false);
  const running = React.useRef(false);
  const labelId = React.useId();
  const record: Row | null = context.record;
  const id = rowPublicId(record);
  const type = metadata?.resource.resourceType;
  const tags = Array.isArray(record?.tags) ? record.tags : [];
  const disabled = readOnly || context.actionsBlocked || !holdsPermission(record, "write") || preview.viewAs || preview.pending || pending;
  if (!id || !type || !relation) return null;

  const change = async (next: readonly unknown[]): Promise<void> => {
    if (disabled || running.current) return;
    running.current = true;
    setPending(true);
    try {
      const wanted = relationIdList(next);
      const previous = relationIdList(tags);
      const added = wanted.filter((id) => !previous.includes(id));
      const removed = previous.filter((id) => !wanted.includes(id));
      if (added.length) await tag({ type, id, tags: added });
      if (removed.length) await untag({ type, id, tags: removed });
    } catch (error) {
      toast.danger({ title: errorMessage(error, t("record.error")) });
    } finally {
      running.current = false;
      setPending(false);
    }
  };
  return <FieldRoot>
    <FieldLabel id={labelId} nativeLabel={false} render={<span />}>{t("record.label")}</FieldLabel>
    <RelationMultiFieldWidget relation={relation} value={tags} aria-label={t("record.label")}
      readOnly={Boolean(disabled)} onChange={(next) => void change(next)} />
  </FieldRoot>;
}

export const recordTagsWidget: WidgetDefinition = {
  edit: RecordTagsWidget,
  read: (props) => <RecordTagsWidget {...props} readOnly />,
};
