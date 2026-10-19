import type * as React from "react";
import type { StatusIconTone } from "@angee/ui";

/** Kinds a Timeline line can have; the presentation each kind declares (`timeline#kinds`). */
export type TimelineKind = "run" | "decision" | "change" | "comment" | "conversation" | "task" | "file" | "page" | "source" | "correspondence" | "access";

export interface KindPresentation { label: string; icon: string; tone: StatusIconTone; verb: string }

export const KIND_PRESENTATION: Record<TimelineKind, KindPresentation> = {
  run: { label: "Runs", icon: "activity", tone: "info", verb: "Open run" },
  decision: { label: "Decisions", icon: "check", tone: "success", verb: "Open decision" },
  change: { label: "Changes", icon: "pencil", tone: "muted", verb: "View change" },
  comment: { label: "Comments", icon: "comments", tone: "info", verb: "Open in Discussion" },
  conversation: { label: "Conversations", icon: "link", tone: "muted", verb: "Open conversation" },
  task: { label: "Tasks", icon: "check", tone: "success", verb: "Open task" },
  file: { label: "Files", icon: "file", tone: "muted", verb: "Open file" },
  page: { label: "Pages", icon: "notes", tone: "muted", verb: "Open page" },
  source: { label: "Sources", icon: "link", tone: "muted", verb: "Open source" },
  correspondence: { label: "Correspondence", icon: "comments", tone: "info", verb: "Open message" },
  access: { label: "Access", icon: "lock", tone: "warning", verb: "Manage access" },
};

/** One Timeline line: a pointer, never a body. */
export interface TimelineLine { id: string; at: string; kind: TimelineKind; title: string; actor: string; detail?: string; needsMe?: boolean; folded?: string }

export interface RelatedGroup { id: string; label: string; count: number; verb?: string; rows: readonly { label: string; meta: string }[] }
export interface TaskItem { title: string; due: string; who: string; done?: boolean }
export interface Message { who: string; at: string; text: string; kind?: "note" | "email" }

/** What the context aside shows for one record; a missing key means the tab is absent. */
export interface AsideData {
  kinds: readonly TimelineKind[];
  lines: readonly TimelineLine[];
  discussion?: { messages: readonly Message[] };
  tasks?: readonly TaskItem[];
  related?: readonly RelatedGroup[];
}

export type AsideTab = "timeline" | "discussion" | "tasks" | "related";

/** A verb on the command band: lifecycle verbs only; everything else is in the menu. */
export interface LifecycleVerb { label: string; tone?: "primary" | "danger" }

export interface HeaderControls { access: readonly string[]; following: boolean; needsMe: number; pager?: string }

/** The explorer on the left: one root selector (with its views), rows, create verb. */
export interface ExplorerData {
  root: string;
  view?: string;
  lenses?: readonly { label: string; active?: boolean }[];
  create?: string;
  rows: readonly { label: string; meta?: string; active?: boolean; depth?: number; icon?: string; tone?: "warning" | "success" }[];
}

export interface SubjectData {
  eyebrow: string;
  title: string;
  subtitle?: string;
  steps?: readonly { value: string; label: string }[];
  step?: string;
  /** What blocks the next lifecycle step; shown under the statusbar, never on the band. */
  waits?: string;
  lifecycle: readonly LifecycleVerb[];
  menu: readonly string[];
  controls: HeaderControls;
  /** The record's own content: fields and body tabs, or the thing itself (preview, editor). */
  body: React.ReactNode;
  /** A source document shown beside the form (invoice): the evidence the record was read from. */
  document?: { title: string; meta: string; preview: React.ReactNode };
}
