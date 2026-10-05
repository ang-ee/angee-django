import * as React from "react";

import { useInAppLink } from "../../lib/in-app-link";
import { Avatar, avatarInitials } from "../../ui/avatar";
import type { GanttEvent } from "./GanttView";

export interface GanttLanePerson {
  id: string;
  name: string;
  image?: string | null;
}

/** The lane resource supplies presentation facts; missing secondary parts are omitted. */
export interface GanttLaneDetails {
  title: string;
  href?: string;
  secondary?: string | null;
  people?: readonly GanttLanePerson[];
  /** Short annotation shown after the lane's last bar. */
  note?: string | null;
}

/** Shared label for collection Gantt lanes. The record link is a real anchor when declared. */
export function GanttLane({ details, onOpen }: {
  details: GanttLaneDetails;
  onOpen?: () => void;
}): React.ReactElement {
  const { title, href, secondary, people } = details;
  const titleClass = "block max-w-full truncate text-start font-medium text-brand hover:underline";
  const visiblePeople = people?.filter((person) => person.name.trim());
  const link = useInAppLink(href, { onClick: (event) => event.stopPropagation() }, { navigate: onOpen });
  return (
    <div className="flex min-w-0 flex-col gap-0.5 py-1">
      {href ? <a className={titleClass} href={href} title={title} {...link}>{title}</a>
        : onOpen ? <button type="button" className={titleClass} onClick={(event) => { event.stopPropagation(); onOpen(); }} title={title}>{title}</button>
          : <span className="block truncate font-medium" title={title}>{title}</span>}
      {secondary?.trim() ? <span className="block truncate text-xs text-fg-muted" title={secondary}>{secondary}</span> : null}
      {visiblePeople?.length ? <div className="flex min-w-0 items-center gap-2 overflow-hidden">
        {visiblePeople.map((person) => <span key={person.id} className="inline-flex min-w-0 shrink items-center gap-1 text-xs text-fg-muted">
          <Avatar size="sm" initials={avatarInitials(person.name)} src={person.image ?? undefined} aria-hidden />
          <span className="truncate">{person.name}</span>
        </span>)}
      </div> : null}
    </div>
  );
}

/** Attach each lane's annotation to its chronologically last scheduled bar. */
export function withGanttLaneNotes(
  events: readonly GanttEvent[],
  detailsByLane: ReadonlyMap<string, GanttLaneDetails>,
): GanttEvent[] {
  const notes = new Map<string, string>([...detailsByLane].flatMap(([id, details]) => {
    const note = details.note?.trim();
    return note ? [[id, note] as const] : [];
  }));
  const lastByLane = new Map<string, GanttEvent>();
  for (const event of events) {
    if (!event.resourceId || !notes.has(event.resourceId)) continue;
    const previous = lastByLane.get(event.resourceId);
    if (!previous || event.end > previous.end) lastByLane.set(event.resourceId, event);
  }
  return events.map((event) => lastByLane.get(event.resourceId ?? "") === event
    ? { ...event, note: notes.get(event.resourceId ?? "") }
    : event);
}
