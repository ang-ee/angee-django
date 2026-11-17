import type { AnyCalendarWindowSource, ResourceRecordHrefLookup } from "@angee/ui";

/** The calendar page's container of occurrence sources other addons overlay on its events. */
export const OCCURRENCE_SOURCES = "calendar.events#occurrence-sources";

/** What a contributed occurrence source reads from the calendar page that shows it. */
export interface OccurrenceSourceContext {
  /** The signed-in viewer's public user id; blank when nobody is signed in. */
  viewerId: string;
  /** Opens a record of any routed model through the resource-route owner. */
  recordHref: ResourceRecordHrefLookup;
}

/**
 * One occurrence source an addon overlays on the calendar page. The page calls it
 * with its context and fetches the returned source beside its own events. The
 * source's key is the container child's id, which the page sets, so a source
 * with a `label` gets a toolbar toggle under the id the addon declared.
 */
export type OccurrenceSourceContribution = (context: OccurrenceSourceContext) => Omit<AnyCalendarWindowSource, "key">;

declare module "@angee/ui/runtime" {
  interface ContainerKinds {
    /** Occurrence sources the calendar page overlays on its events (`calendar.events#occurrence-sources`). */
    "occurrence-sources": OccurrenceSourceContribution;
  }
}
