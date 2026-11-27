import * as React from "react";
import { holdsPermission, type Row } from "@angee/metadata";
import { Alert } from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";

/** Override, where the server admits the reader and the shipped default has no override yet. */
export function offersOverride(record: Row): boolean {
  return record.can_override === true && record.is_overridden !== true;
}

/** Keep override, where the server admits the reader and the shipped default changed since. */
export function offersKeepOverride(record: Row): boolean {
  return record.can_keep_override === true && record.has_updated_default === true;
}

/**
 * What the open row is to this deployment, read from the row facts the server
 * projects: a shipped default, overridden or not; an override whose shipped
 * default changed; or a row whose saved changes go live, and, for an override,
 * that deleting it returns to the shipped default. Its facts show to every
 * reader; each hint shows only where the verb it names is offered: Override and
 * Keep override as the form's actions offer them, Save and Delete where the row
 * projects `write` and `delete`. A row that is none of these to its reader shows
 * nothing, and so does a create form, which has no row.
 */
export function MessageTemplateNotice({ record }: { record: Row | null }): React.ReactElement | null {
  const t = useMessagingTemplatesT();
  if (!record) return null;
  const hint = (admitted: boolean, key: string) => admitted ? [t(key)] : [];
  const notice = record.is_baseline === true
    ? record.is_overridden === true
      ? { tone: "info" as const, facts: [t("notice.overridden")], hints: [] }
      : {
          tone: "info" as const,
          facts: [t("notice.shipped")],
          hints: hint(offersOverride(record), "notice.shippedOverride"),
        }
    : record.has_updated_default === true
      ? {
          tone: "warning" as const,
          facts: [t("notice.defaultUpdated")],
          hints: [
            ...hint(offersKeepOverride(record), "notice.keepHint"),
            ...hint(holdsPermission(record, "delete"), "notice.deleteNew"),
          ],
        }
      : {
          tone: "info" as const,
          facts: [],
          hints: [
            ...hint(holdsPermission(record, "write"), "notice.live"),
            ...hint(record.overrides_default === true && holdsPermission(record, "delete"), "notice.deleteDefault"),
          ],
        };
  if (notice.facts.length === 0 && notice.hints.length === 0) return null;
  return (
    <Alert tone={notice.tone} title={notice.facts.length ? notice.facts.join(" ") : undefined}>
      {notice.hints.length ? notice.hints.join(" ") : null}
    </Alert>
  );
}
