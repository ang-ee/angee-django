import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";

/** @deprecated Revisions are retired; angee 0.4.0 removes this export. */
export interface RevisionsTabProps {
  resource: string;
  recordId: string | null | undefined;
  enabled?: boolean;
}

/**
 * The retired revisions tab, kept so a consumer's import compiles until angee 0.4.0.
 *
 * Revisions were django-reversion's and left with it. A record's changes are its
 * History tab, which the provenance addon contributes to every form whose resource
 * keeps history. This component reads nothing and says so.
 *
 * @deprecated Remove the tab that renders it; angee 0.4.0 removes this export.
 */
export function RevisionsTab(_props: RevisionsTabProps): React.ReactElement {
  const t = useUiT();
  return (
    <EmptyState
      icon="activity"
      title={t("revisions.unavailable")}
      description={t("revisions.retired")}
      className="min-h-48 p-4"
    />
  );
}
