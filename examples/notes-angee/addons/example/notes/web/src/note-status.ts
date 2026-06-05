import type { BadgeVariant, WidgetOption } from "@angee/base";

/** The lifecycle a note moves through. */
export type NoteStatus = "DRAFT" | "IN_REVIEW" | "ACTIVE" | "ARCHIVED";

/** Choices for the status select, in lifecycle order. */
export const NOTE_STATUS_OPTIONS: readonly WidgetOption[] = [
  { value: "DRAFT", label: "Draft" },
  { value: "IN_REVIEW", label: "In Review" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

/** Badge tone per status for the list column. */
export const NOTE_STATUS_TONES: Record<string, BadgeVariant> = {
  ACTIVE: "success",
  DRAFT: "warning",
  IN_REVIEW: "info",
  ARCHIVED: "default",
  active: "success",
  draft: "warning",
  in_review: "info",
  archived: "default",
};
