import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { CALENDAR_MODEL } from "../resources";
import { calendarForm } from "./CalendarForm";

/**
 * The calendars collection: whose each calendar is and how it takes overlaps. A
 * calendar's record page carries the platform's share control, driven by the
 * calendar's grantable relations (reader, editor, free/busy).
 */
export function CalendarsPage(): React.ReactElement {
  return (
    <ResourceList resource={CALENDAR_MODEL} form={calendarForm} placement="inline" routed>
      <List resource={CALENDAR_MODEL}>
        <Column field="name" />
        <Column field="party" />
        <Column field="overlap_policy" />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
