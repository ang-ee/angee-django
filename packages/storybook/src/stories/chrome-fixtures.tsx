import type {
  AppChooserItem,
  ChromeMenuItem,
  SpotlightCommand,
  TopMenuTab,
} from "@angee/ui";

export const chromeMenuItems: readonly ChromeMenuItem[] = [
  {
    id: "notes",
    label: "Notes",
    to: "/notes",
    icon: "notes",
    description: "Records, drafts, and publishing queues.",
    tone: "brand",
  },
  {
    id: "files",
    label: "Files",
    to: "/files",
    icon: "files",
    description: "Assets, uploads, and generated exports.",
    tone: "info",
  },
  {
    id: "reports",
    label: "Reports",
    to: "/reports",
    icon: "reports",
    description: "Operational dashboards and saved summaries.",
    tone: "success",
  },
  {
    id: "platform",
    label: "Platform",
    icon: "settings",
    group: "platform",
    children: [
      {
        id: "iam",
        label: "IAM",
        to: "/iam",
        icon: "auth",
        description: "Users, groups, and access policy.",
        group: "platform",
        tone: "neutral",
      },
      {
        id: "resources",
        label: "Resources",
        to: "/resources",
        icon: "archive",
        description: "Imports, exports, and tier manifests.",
        group: "platform",
        tone: "warning",
      },
    ],
  },
];

export const appChooserItems: readonly AppChooserItem[] = [
  {
    id: "notes",
    label: "Notes",
    to: "/notes",
    icon: "notes",
    description: "Daily operating records",
    tone: "brand",
  },
  {
    id: "files",
    label: "Files",
    to: "/files",
    icon: "files",
    description: "Uploaded source material",
    tone: "info",
  },
  {
    id: "reports",
    label: "Reports",
    to: "/reports",
    icon: "reports",
    description: "Saved analytics",
    tone: "success",
  },
  {
    id: "activity",
    label: "Activity",
    to: "/activity",
    icon: "activity",
    description: "Recent workspace changes",
    tone: "neutral",
    badge: 3,
  },
  {
    id: "iam",
    label: "IAM",
    to: "/iam",
    icon: "auth",
    description: "Identity and permissions",
    group: "platform",
    tone: "neutral",
  },
  {
    id: "resources",
    label: "Resources",
    to: "/resources",
    icon: "archive",
    description: "Resource manifests",
    group: "platform",
    tone: "warning",
  },
  {
    id: "calendar",
    label: "Calendar",
    to: "#",
    icon: "calendar",
    description: "Scheduling surfaces",
    status: "future",
    tone: "neutral",
  },
];

// Tabs are presentational here; a product route reads `?tab=` to apply its own
// filter (e.g. starred/archived). The framework owns only the strip.
export const topMenuTabs: readonly TopMenuTab[] = [
  { id: "all", label: "All notes", icon: "list" },
  { id: "starred", label: "Starred", icon: "star" },
  { id: "archive", label: "Archive", icon: "archive" },
];

export const spotlightCommands: readonly SpotlightCommand[] = [
  {
    id: "notes.open",
    title: "Open notes",
    icon: "notes",
    group: "Navigation",
    hint: "G N",
    run: () => undefined,
  },
  {
    id: "reports.open",
    title: "Open reports",
    icon: "reports",
    group: "Navigation",
    hint: "G R",
    run: () => undefined,
  },
  {
    id: "record.star",
    title: "Star current record",
    icon: "star",
    group: "Record",
    hint: "S",
    run: () => undefined,
  },
  {
    id: "resources.sync",
    title: "Sync resources",
    icon: "archive",
    group: "System",
    hint: "Cmd+Shift+R",
    run: () => undefined,
  },
];

export function LayoutStoryBody() {
  return (
    <div className="grid gap-4 p-6">
      <section className="rounded-8 border border-border-subtle bg-sheet p-5">
        <div className="text-15 font-semibold text-fg">Notes workspace</div>
        <p className="mt-1 max-w-2xl text-13 text-fg-muted">
          A dense layout body with enough surface area to verify rail, top bar,
          breadcrumbs, and chatter placement.
        </p>
      </section>
      <div className="grid gap-3 md:grid-cols-3">
        {["Backlog", "In review", "Published"].map((label) => (
          <div
            key={label}
            className="min-h-32 rounded-8 border border-border-subtle bg-sheet p-4"
          >
            <div className="text-13 font-semibold text-fg">{label}</div>
            <div className="mt-2 h-2 rounded-full bg-inset" />
            <div className="mt-2 h-2 w-2/3 rounded-full bg-inset" />
          </div>
        ))}
      </div>
    </div>
  );
}
