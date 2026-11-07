import { holdsPermission, refineResourceName, useModelMetadata } from "@angee/metadata";
import { useUpdate } from "@refinedev/core";
import { Button, Glyph, useRecordChromeContext, useRuntimeViewAs } from "@angee/ui";
import type { ReactElement } from "react";

import { useNotesT } from "./i18n";

/** Note-specific star chrome; IAM owns the global Share action beside it. */
export function RecordChrome(): ReactElement | null {
  const t = useNotesT();
  const context = useRecordChromeContext();
  const metadata = useModelMetadata(context.resource);
  const preview = useRuntimeViewAs();
  const update = useUpdate({
    resource: refineResourceName(metadata?.resource ?? null),
    dataProviderName: context.dataProviderName,
    invalidates: ["list", "many", "detail"],
  });
  if (context.resource !== "notes.Note") return null;
  const starred = context.record?.is_starred;
  const disabled = typeof starred !== "boolean" || context.formReadOnly || context.actionsBlocked
    || !holdsPermission(context.record, "write") || preview.viewAs || preview.pending || update.mutation.isPending;
  return (
    <Button type="button" variant="ghost" size="iconSm" aria-label={t("record.star")}
      aria-pressed={starred === true} disabled={Boolean(disabled)}
      onClick={() => {
        if (disabled) return;
        void update.mutateAsync({ id: context.recordId, values: { is_starred: !starred } }).catch(() => undefined);
      }}>
      <Glyph name="star" className={starred ? "fill-current" : undefined} />
    </Button>
  );
}
