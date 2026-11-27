import { useId, type ReactElement } from "react";

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
  /**
   * The form's label for a field (a record pane passes its context's `fieldLabel`): an issue that
   * names a field is titled by it, and the control that focuses the field is described by it, so
   * two issues with the same words under two fields read apart on screen and to a screen reader.
   */
  fieldLabel?: (field: string) => string | undefined;
  /**
   * Announce the issues politely, a danger one included, which is otherwise an alert and
   * interrupts a screen reader: for issues about edits a person has not submitted yet, which
   * change while they type elsewhere. A refusal of a submit stays an alert. The fragment then
   * renders one polite live region that stays in the page with no issues too (an empty block),
   * since a screen reader announces what arrives in a region it already knows, not a region
   * that arrives with its text; the issues inside it carry no role of their own, and each is
   * read whole when it changes, its field's label with its message. The empty block costs
   * nothing in a stack spaced by margins and one gap in a flex or grid stack spaced by `gap`.
   */
  polite?: boolean;
}

/** Render issue messages with shared tones and optional focus or routed links.
 * @kind fragment
 */
export function RecordIssues({ items, onFocusField, fieldLabel, polite }: RecordIssuesProps): ReactElement | null {
  const recordHref = useResourceRecordHrefLookup();
  const titleIds = useId();
  const list = items.length ? <ul className="space-y-2">
    {items.map((item, index) => {
      const field = item.field;
      const href = item.record ? recordHref(item.record.resource, item.record.id) : undefined;
      const label = field ? fieldLabel?.(field) : undefined;
      const titleId = label ? `${titleIds}-${index}` : undefined;
      const message = field && onFocusField
        ? <NavLink
          render={<button type="button" />}
          className="text-left [overflow-wrap:anywhere]"
          onClick={() => onFocusField(field, item.recordTabId ? { recordTabId: item.recordTabId } : undefined)}
          variant="inline"
          aria-describedby={titleId}
        >{item.message}</NavLink>
        : href
          ? <NavLink href={href} variant="inline" className="[overflow-wrap:anywhere]">{item.message}</NavLink>
          : item.message;
      return <li key={item.id} {...(polite ? { "aria-atomic": true } : {})}>
        <Banner format="alert" tone={item.tone} {...(polite ? { role: "none" } : {})}
          {...(label ? { title: <span id={titleId}>{label}</span> } : {})}>{message}</Banner>
      </li>;
    })}
  </ul> : null;
  return polite ? <div aria-live="polite">{list}</div> : list;
}
