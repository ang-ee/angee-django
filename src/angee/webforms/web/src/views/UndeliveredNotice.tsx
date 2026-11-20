import * as React from "react";
import {
  Alert, NavLink, createResourceViewState, resourceViewStateToSearch, useResourceRoute,
  type RecordToolbarContext,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { RESPONSE_MODEL } from "../resources";
import { undeliveredFilter, undeliveredSummary } from "../undelivered";

/**
 * The notice a form's record wears while responses wait for delivery: how many
 * there are, when the first of them is removed, and a link to exactly those
 * responses. A form's retention removes a response whether or not it was
 * delivered, so the ones still waiting are shown before that day.
 */
export function UndeliveredNotice({ recordId, record }: Pick<RecordToolbarContext, "recordId" | "record">): React.ReactElement | null {
  const t = useWebformsT();
  const search = React.useMemo(
    () => resourceViewStateToSearch(createResourceViewState({ filter: undeliveredFilter(recordId ?? undefined) })),
    [recordId],
  );
  const responses = useResourceRoute(RESPONSE_MODEL, search);
  const summary = record ? undeliveredSummary(record, t) : null;
  if (!summary) return null;
  return (
    <Alert tone="warning" title={summary}
      actions={responses ? <NavLink href={responses} variant="inline">{t("form.undeliveredShow")}</NavLink> : undefined}>
      {record?.undelivered_expires_at ? t("form.undeliveredHint") : null}
    </Alert>
  );
}
