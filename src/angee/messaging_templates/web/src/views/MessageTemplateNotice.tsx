import * as React from "react";
import { holdsPermission, type Row } from "@angee/metadata";
import { Alert } from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";

/**
 * What the open row is to this deployment, read from the row facts the server
 * projects: a shipped default, overridden or not; an override whose shipped
 * default changed; or a row whose saved changes go live. Its facts show to every
 * reader; each hint shows only to a reader the row's projected permissions admit
 * to the verb it names. A row that is none of these to its reader shows nothing,
 * and so does a create form, which has no row.
 */
export function MessageTemplateNotice({ record }: { record: Row | null }): React.ReactElement | null {
  const t = useMessagingTemplatesT();
  if (!record) return null;
  const hint = (permission: string, key: string) => holdsPermission(record, permission) ? [t(key)] : [];
  const notice = record.is_baseline === true
    ? record.is_overridden === true
      ? { tone: "info" as const, facts: [t("notice.overridden")], hints: [] }
      : { tone: "info" as const, facts: [t("notice.shipped")], hints: hint("preview", "notice.shippedOverride") }
    : record.has_updated_default === true
      ? {
          tone: "warning" as const,
          facts: [t("notice.defaultUpdated")],
          hints: [...hint("write", "notice.keepHint"), ...hint("delete", "notice.deleteNew")],
        }
      : {
          tone: "info" as const,
          facts: [],
          hints: [...hint("write", "notice.live"), ...hint("delete", "notice.deleteDefault")],
        };
  if (notice.facts.length === 0 && notice.hints.length === 0) return null;
  return (
    <Alert tone={notice.tone} title={notice.facts.length ? notice.facts.join(" ") : undefined}>
      {notice.hints.length ? notice.hints.join(" ") : null}
    </Alert>
  );
}
