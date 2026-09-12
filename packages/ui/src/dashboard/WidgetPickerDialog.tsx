import * as React from "react";
import { useSchemaFieldMetadata } from "@angee/metadata";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import {
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "../ui/dialog";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import type { DashboardRegistry } from "./headless";
import {
  buildDashboardWidgetCatalogue,
  type DashboardWidgetCatalogueEntry,
} from "./catalogue";

const RESULT_LIMIT = 120;

export function DashboardWidgetPickerDialog({
  open,
  onOpenChange,
  onPick,
  preferredResource,
  registry,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (entry: DashboardWidgetCatalogueEntry) => void;
  preferredResource?: string;
  registry: DashboardRegistry;
}): React.ReactElement {
  const metadata = useSchemaFieldMetadata();
  const entries = React.useMemo(
    () => buildDashboardWidgetCatalogue(metadata, registry, preferredResource),
    [metadata, preferredResource, registry],
  );
  const [query, setQuery] = React.useState("");
  const [kind, setKind] = React.useState("");
  const [section, setSection] = React.useState("");

  React.useEffect(() => {
    if (open) return;
    setQuery("");
    setKind("");
    setSection("");
  }, [open]);

  const kindOptions = React.useMemo(() => {
    const labels = new Map(entries.map((entry) => [entry.kind, entry.kindLabel]));
    return [...labels]
      .map(([value, label]) => ({ value, label }))
      .sort((left, right) => left.label.localeCompare(right.label));
  }, [entries]);
  const sectionOptions = React.useMemo(
    () => [...new Set(entries.map((entry) => entry.section))]
      .sort()
      .map((value) => ({ value, label: value })),
    [entries],
  );
  const matches = React.useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return entries.filter((entry) => {
      if (kind && entry.kind !== kind) return false;
      if (section && entry.section !== section) return false;
      const text = `${entry.title} ${entry.section} ${entry.resource} ${entry.kindLabel}`.toLowerCase();
      return terms.every((term) => text.includes(term));
    });
  }, [entries, kind, query, section]);
  const visible = matches.slice(0, RESULT_LIMIT);
  const grouped = React.useMemo(() => {
    const result = new Map<string, DashboardWidgetCatalogueEntry[]>();
    for (const entry of visible) {
      const group = result.get(entry.section) ?? [];
      group.push(entry);
      result.set(entry.section, group);
    }
    return [...result];
  }, [visible]);

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent size="lg" className="max-h-[min(44rem,calc(100vh-2rem))] overflow-hidden">
          <DialogHeader>
            <DialogTitle>Add a widget</DialogTitle>
            <DialogDescription>
              Pick the question to add. Its data source and first useful view are already configured.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="grid min-h-0 gap-3 overflow-hidden">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <Glyph name="search" size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-fg-subtle" />
                <Input
                  autoFocus
                  aria-label="Search widgets"
                  className="pl-8"
                  placeholder="Search widgets, resources, or sections…"
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                />
              </div>
              <Select
                aria-label="Widget section"
                className="sm:w-48"
                options={[{ value: "", label: "All sections" }, ...sectionOptions]}
                value={section}
                onValueChange={setSection}
              />
            </div>
            <div className="flex flex-wrap items-center gap-1.5" aria-label="Widget type">
              <span className="mr-1 text-11 text-fg-muted">Type</span>
              {[{ value: "", label: "All" }, ...kindOptions].map(({ value, label }) => (
                <button
                  key={value || "__all__"}
                  type="button"
                  aria-pressed={kind === value}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-11 transition-colors focus-visible:focus-ring",
                    kind === value
                      ? "border-brand bg-brand-soft text-brand-soft-text"
                      : "border-border text-fg-muted hover:bg-inset hover:text-fg",
                  )}
                  onClick={() => setKind(value)}
                >
                  {label}
                </button>
              ))}
              <span className="ml-auto text-11 text-fg-subtle">
                {matches.length.toLocaleString()} {matches.length === 1 ? "widget" : "widgets"}
              </span>
            </div>
            <div className="min-h-0 overflow-y-auto pr-1">
              {grouped.length === 0 ? (
                <div className="grid min-h-40 place-content-center text-center">
                  <p className="text-13 font-medium text-fg">No matching widgets</p>
                  <p className="mt-1 text-12 text-fg-muted">Try another search or clear a filter.</p>
                </div>
              ) : (
                <div className="grid gap-4">
                  {grouped.map(([group, choices]) => (
                    <section key={group} className="grid gap-1">
                      <h3 className="sticky top-0 z-10 bg-sheet py-1 text-2xs font-semibold tracking-wide text-fg-muted uppercase">
                        {group}
                      </h3>
                      {choices.map((entry) => (
                        <button
                          key={entry.id}
                          type="button"
                          className="flex min-h-10 items-center gap-3 rounded-6 border border-transparent px-2 py-1.5 text-left outline-none hover:border-border hover:bg-inset focus-visible:focus-ring"
                          onClick={() => {
                            onPick(entry);
                            onOpenChange(false);
                          }}
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-13 font-medium text-fg">{entry.title}</span>
                            <span className="block truncate text-11 text-fg-subtle">{entry.resource}</span>
                          </span>
                          <span className="shrink-0 rounded-full bg-inset px-2 py-0.5 text-11 text-fg-muted">
                            {entry.kindLabel}
                          </span>
                        </button>
                      ))}
                    </section>
                  ))}
                  {matches.length > visible.length ? (
                    <p className="pb-2 text-center text-11 text-fg-subtle">
                      Showing the first {visible.length.toLocaleString()}. Search or choose a section to narrow the list.
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          </DialogFooter>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}
