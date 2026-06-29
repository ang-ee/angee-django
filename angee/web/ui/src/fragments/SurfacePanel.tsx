import * as React from "react";

import { cn } from "../lib/cn";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { textRoleVariants } from "../ui/text";

export interface SurfacePanelProps {
  title: string;
  summary?: string;
  actions?: React.ReactNode;
  children: React.ReactElement;
}

export function SurfacePanel({
  title,
  summary,
  actions,
  children,
}: SurfacePanelProps): React.ReactElement {
  return (
    <Card asChild className="overflow-hidden shadow-none">
      <section>
        <CardHeader
          className="flex-row items-center justify-between gap-3 border-b border-border-subtle px-4 py-3"
          density="md"
        >
          <div className="min-w-0">
            <CardTitle className="truncate text-15" density="md">
              {title}
            </CardTitle>
          </div>
          {summary || actions ? (
            <div className="flex shrink-0 items-center gap-2">
              {summary ? (
                <span className={textRoleVariants({ role: "caption" })}>{summary}</span>
              ) : null}
              {actions}
            </div>
          ) : null}
        </CardHeader>
        <CardContent className="p-0" density="md">
          {children}
        </CardContent>
      </section>
    </Card>
  );
}

// Static class maps so Tailwind's JIT scanner sees every emitted utility (it
// cannot follow values composed from props); see HeroPage for the same pattern.
const settingsShellMaxWidth = {
  "1100": "max-w-[1100px]",
  "1200": "max-w-[1200px]",
} as const;

const settingsShellGap = {
  "6": "gap-6",
  "8": "gap-8",
  "10": "gap-10",
} as const;

export interface SettingsShellProps {
  /** Centered column max-width in px; pages differ (1100 vs 1200). */
  maxWidth: keyof typeof settingsShellMaxWidth;
  /** Vertical gap between sections, on the spacing scale (6/8/10). */
  gap: keyof typeof settingsShellGap;
  className?: string;
  children?: React.ReactNode;
}

/**
 * The centered settings/admin column: a `mx-auto` flex column with the standard
 * page gutters. `maxWidth` and `gap` parameterize the only facts settings pages
 * vary; everything else is shared chrome.
 */
export function SettingsShell({
  maxWidth,
  gap,
  className,
  children,
}: SettingsShellProps): React.ReactElement {
  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col px-6 py-6 sm:px-8",
        settingsShellMaxWidth[maxWidth],
        settingsShellGap[gap],
        className,
      )}
    >
      {children}
    </div>
  );
}

export interface SettingsSectionProps {
  title: string;
  /** Optional sub-heading rendered under the title. */
  description?: string;
  className?: string;
  children?: React.ReactNode;
}

/**
 * A card-less titled section for settings/admin surfaces: an `<h2>` heading with
 * an optional description, followed by content. Unlike `SurfacePanel` /
 * `DetailSection`, it adds no card chrome — the wrapped view brings its own.
 */
export function SettingsSection({
  title,
  description,
  className,
  children,
}: SettingsSectionProps): React.ReactElement {
  return (
    <section className={cn("grid gap-3", className)}>
      {description ? (
        <header className="grid gap-0.5">
          <h2 className={textRoleVariants({ role: "title" })}>{title}</h2>
          <p className={textRoleVariants({ role: "meta" })}>{description}</p>
        </header>
      ) : (
        <h2 className={textRoleVariants({ role: "title" })}>{title}</h2>
      )}
      {children}
    </section>
  );
}
