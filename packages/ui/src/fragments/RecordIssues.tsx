import type { ReactElement } from "react";

import type { FeedbackIntent } from "../lib/tones";
import { useResourceRecordHrefLookup } from "../runtime";
import { Banner } from "../ui/alert";
import { NavLink } from "../ui/nav-link";
import type { RecordFieldFocusOptions } from "../views/form/form-view-surface";

/** One caller-owned issue and its optional field or record destination. */
export interface RecordIssue {
  id: string;
  tone: FeedbackIntent;
  message: string;
  field?: string;
  recordTabId?: string;
  record?: { resource: string; id: string };
}

/** Shared record feedback; field focus uses the form's existing tab-aware contract. */
export interface RecordIssuesProps {
  items: readonly RecordIssue[];
  onFocusField?: (field: string, options?: RecordFieldFocusOptions) => void;
}

/** Render issue messages with shared tones and optional focus or routed links. */
export function RecordIssues({ items, onFocusField }: RecordIssuesProps): ReactElement | null {
  const recordHref = useResourceRecordHrefLookup();
  if (!items.length) return null;

  return <ul className="space-y-2">
    {items.map((item) => {
      const field = item.field;
      const href = item.record ? recordHref(item.record.resource, item.record.id) : undefined;
      const message = field && onFocusField
        ? <NavLink
          render={<button type="button" />}
          className="text-left [overflow-wrap:anywhere]"
          onClick={() => onFocusField(field, item.recordTabId ? { recordTabId: item.recordTabId } : undefined)}
          variant="inline"
        >{item.message}</NavLink>
        : href
          ? <NavLink href={href} variant="inline" className="[overflow-wrap:anywhere]">{item.message}</NavLink>
          : item.message;
      return <li key={item.id}>
        <Banner format="alert" tone={item.tone}>{message}</Banner>
      </li>;
    })}
  </ul>;
}
