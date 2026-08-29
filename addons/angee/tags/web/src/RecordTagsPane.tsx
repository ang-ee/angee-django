import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Chip, EmptyState, LoadingPanel, RemovableChip, SectionEyebrow, errorMessage } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

import {
  TagAssignmentsDocument,
  TagDocument,
  TagOptionsDocument,
  UntagDocument,
} from "./documents.console";
import { useTagsT } from "./i18n";

// The authored reads register against `tags.Tag`; the mutations invalidate that key.
const TAG_MODELS = ["tags.Tag"] as const;

interface TagOption {
  id: string;
  name: string;
  color: string;
}

/** A colour swatch for one tag, tinted from the tag's stored hex colour. */
function Swatch({ color }: { color: string }): React.ReactElement {
  return (
    <span
      aria-hidden
      className="inline-block size-2 shrink-0 rounded-full"
      style={{ backgroundColor: color }}
    />
  );
}

/**
 * The record-tags widget (P-d): the tags applied to the active record, with a
 * palette of the remaining vocabulary to add from. Contributed as a chatter tab
 * (`@angee/tags` manifest), so it renders in the console record aside for every
 * record without any change to the addon that owns the record — the party detail
 * page included. The tab self-gates to an empty state off a record view.
 *
 * `context.view.type` is the record's REBAC resource type (the backend's
 * `target_type`, e.g. `parties/party`) and `context.view.sqid` its public id, so
 * the same pane tags any polymorphic target through the authored edge.
 */
export function RecordTagsPane({
  context,
}: {
  context: ChatterViewContext;
}): React.ReactElement {
  const t = useTagsT();
  const isRecord = context.view.kind === "record";
  const targetType = context.view.type;
  const targetId = isRecord ? context.view.sqid : undefined;
  const enabled = isRecord && Boolean(targetType && targetId);

  const assigned = useAuthoredQuery(
    TagAssignmentsDocument,
    { targetType, targetId: targetId ?? "" },
    { enabled, models: TAG_MODELS },
  );
  const vocabulary = useAuthoredQuery(
    TagOptionsDocument,
    {},
    { enabled, models: TAG_MODELS },
  );

  // Correct as-is: TagAssignmentsDocument is an authored query keyed by TAG_MODELS.
  const [applyTags, applyState] = useAuthoredMutation(TagDocument, {
    invalidateModels: TAG_MODELS,
  });
  // Correct as-is: TagAssignmentsDocument is an authored query keyed by TAG_MODELS.
  const [removeTags, removeState] = useAuthoredMutation(UntagDocument, {
    invalidateModels: TAG_MODELS,
  });
  const busy = applyState.fetching || removeState.fetching;

  const add = React.useCallback(
    async (tagId: string) => {
      if (!targetId) return;
      await applyTags({ targetType, targetId, tagIds: [tagId] });
    },
    [applyTags, targetType, targetId],
  );
  const remove = React.useCallback(
    async (tagId: string) => {
      if (!targetId) return;
      await removeTags({ targetType, targetId, tagIds: [tagId] });
    },
    [removeTags, targetType, targetId],
  );

  if (!enabled) {
    return <EmptyState icon="tag" title={t("pane.assigned")} description={t("pane.empty.record")} />;
  }
  if (assigned.fetching && !assigned.data) {
    return <LoadingPanel />;
  }
  if (assigned.error) {
    return (
      <EmptyState
        icon="tag"
        title={t("pane.assigned")}
        description={errorMessage(assigned.error, t("pane.error"))}
      />
    );
  }

  const applied = (assigned.data?.tag_assignments ?? []).flatMap((row) =>
    row.tag ? [row.tag as TagOption] : [],
  );
  const appliedIds = new Set(applied.map((tag) => tag.id));
  const available = (vocabulary.data?.tags ?? []).filter(
    (tag): tag is TagOption => Boolean(tag) && !appliedIds.has(tag.id),
  );

  return (
    <div className="flex flex-col gap-4 p-4">
      <section className="flex flex-col gap-2">
<SectionEyebrow as="h3">{t("pane.assigned")}</SectionEyebrow>
        {applied.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("pane.empty.none")}</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {applied.map((tag) => (
              <li key={tag.id}>
                <RemovableChip
                  tone="neutral"
                  size="sm"
                  className={busy ? "opacity-50" : undefined}
                  removeLabel={tag.name}
                  onRemove={() => {
                    if (!busy) void remove(tag.id);
                  }}
                >
                  <Swatch color={tag.color} />
                  <span className="truncate">{tag.name}</span>
                </RemovableChip>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-2">
<SectionEyebrow as="h3">{t("pane.add")}</SectionEyebrow>
        {available.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("pane.add.empty")}</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {available.map((tag) => (
              <li key={tag.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void add(tag.id)}
                  className="disabled:opacity-50"
                >
                  <Chip tone="muted" size="sm" className="gap-1.5 hover:text-fg">
                    <Swatch color={tag.color} />
                    <span className="truncate">{tag.name}</span>
                  </Chip>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
