import * as React from "react";
import { useSchemaFieldMetadata } from "@angee/metadata";
import { formatNumber } from "../lib/format-number";
import { EmptyState } from "../fragments/EmptyState";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
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
import { SearchInput } from "../ui/input";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { Select } from "../ui/select";
import type { DashboardRegistry } from "./headless";
import {
  buildDashboardWidgetCatalogue,
  type DashboardWidgetCatalogueEntry,
} from "./catalogue";
import { useDashboardT } from "./i18n";

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
  const t = useDashboardT();
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
            <DialogTitle>{t("picker.title")}</DialogTitle>
            <DialogDescription>
              {t("picker.description")}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="grid min-h-0 gap-3 overflow-hidden">
            <div className="flex flex-col gap-2 sm:flex-row">
              <SearchInput
                autoFocus
                aria-label={t("picker.search")}
                className="min-w-0 flex-1"
                placeholder={t("picker.searchPlaceholder")}
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                onClear={() => setQuery("")}
              />
              <Select
                aria-label={t("picker.section")}
                className="sm:w-48"
                options={[{ value: "", label: t("picker.allSections") }, ...sectionOptions]}
                value={section}
                onValueChange={setSection}
              />
            </div>
            <div className="flex flex-wrap items-center gap-1.5" aria-label={t("picker.typeLabel")}>
              <span className="mr-1 text-2xs text-fg-muted">{t("picker.type")}</span>
              {[{ value: "", label: t("picker.all") }, ...kindOptions].map(({ value, label }) => (
                <Chip key={value || "__all__"} size="sm" tone="muted" variant="outline" pressed={kind === value} onClick={() => setKind(value)}>
                  {label}
                </Chip>
              ))}
              <span className="ml-auto text-2xs text-fg-subtle">
                {t("picker.result", { count: matches.length })}
              </span>
            </div>
            <div className="min-h-0 overflow-y-auto pr-1">
              {grouped.length === 0 ? (
                <EmptyState className="min-h-40" title={t("picker.empty.title")} description={t("picker.empty.description")} />
              ) : (
                <div className="grid gap-4">
                  {grouped.map(([group, choices]) => (
                    <section key={group} className="grid gap-1">
                      <SectionEyebrow as="h3" className="sticky top-0 z-10 bg-sheet py-1">
                        {group}
                      </SectionEyebrow>
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
                            <span className="block truncate text-2xs text-fg-subtle">{entry.resource}</span>
                          </span>
                          <Chip size="sm" tone="muted">{entry.kindLabel}</Chip>
                        </button>
                      ))}
                    </section>
                  ))}
                  {matches.length > visible.length ? (
                    <p className="pb-2 text-center text-2xs text-fg-subtle">
                      {t("picker.showingFirst", { count: formatNumber(visible.length) })}
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("picker.cancel")}</Button>
          </DialogFooter>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}
