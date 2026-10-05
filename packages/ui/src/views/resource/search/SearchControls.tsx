import * as React from "react";
import { Filter, QueryParseError } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT } from "../../../i18n";
import { useDebouncedText } from "../../../lib/use-debounced-text";
import { Toolbar } from "../../../ui/toolbar";
import { Toggle } from "../../../ui/toggle";
import { SelectRoot, SelectTrigger, SelectValue, SelectIcon, SelectPortal, SelectPositioner, SelectContent,
  SelectList, SelectItem, SelectItemText, SelectItemIndicator } from "../../../ui/select";
import { PopoverRoot, PopoverTrigger, PopoverPortal, PopoverPositioner, PopoverContent } from "../../../ui/popover";
import { Button } from "../../../ui/button";
import { FilterClauseEditor, type FilterClauseDraft, type FilterClauseField } from "../../../toolbars/FilterClauseEditor";
import { labelText, parseCustomFilterId } from "../utils/labels";
import { GroupStackPanel, groupLevelLabel } from "./GroupStackPanel";
import { SearchBox } from "./SearchBox";
import type { ComposedContainerChild } from "../../../runtime/containers";
import { validateSearchShortcutCatalog, type SearchShortcut } from "./shortcuts";
import type { ResourceSearch } from "./types";

/** Separate controls read the same projection and dispatch the same commands as the box. */
export function SearchControls({ search, shortcuts, box = true, narrow = false }: {
  search: ResourceSearch;
  shortcuts: readonly ComposedContainerChild<SearchShortcut>[];
  box?: true | "collapsed";
  narrow?: boolean;
}): React.ReactElement {
  const t = useUiT();
  const reported = React.useRef(new Set<string>()).current;
  const availableShortcuts = React.useMemo(() => validateSearchShortcutCatalog(shortcuts, search.catalog, { reported }), [shortcuts, search.catalog, reported]);
  const pinned = search.catalog.favorites.filter((favorite) => favorite.pinned);
  return <Toolbar.Root aria-label={t("search.shortcuts")} data-search-shortcuts={availableShortcuts.length || pinned.length ? "" : undefined} className="flex flex-1 flex-wrap gap-2">
    {!narrow ? <>
      {availableShortcuts.filter(({ content }) => content.kind !== "toggle" || !pinned.some((favorite) => favorite.id === content.id))
        .map(({ id, content }) => <Shortcut key={id} search={search} shortcut={content} />)}
      {pinned.map((favorite) => <ToggleShortcut key={favorite.id} search={search} id={favorite.id} />)}
      {search.queryDirty ? <Toolbar.Button onClick={search.clearQuery}>{t("resourceToolbar.clear")}</Toolbar.Button> : null}
    </> : null}
    <SearchBox search={search} box={narrow ? "collapsed" : box} toolbar />
  </Toolbar.Root>;
}

function Shortcut({ search, shortcut }: { search: ResourceSearch; shortcut: SearchShortcut }): React.ReactElement | null {
  switch (shortcut.kind) {
    case "text": return <TextShortcut search={search} shortcut={shortcut} />;
    case "facet": return <FacetShortcut search={search} shortcut={shortcut} />;
    case "clause": return <ClauseShortcut search={search} shortcut={shortcut} />;
    case "toggle": return <ToggleShortcut search={search} id={shortcut.id} label={shortcut.label} />;
    case "group": return search.groupingEnabled ? <GroupShortcut search={search} /> : null;
  }
}

function TextShortcut({ search, shortcut }: { search: ResourceSearch; shortcut: Extract<SearchShortcut, { kind: "text" }> }) {
  const value = search.active.find((item) => item.kind === "text" && item.field === shortcut.field);
  const { draft, setDraft, commit } = useDebouncedText(value?.kind === "text" ? value.value : "", (next) => search.setText(next, shortcut.field));
  const label = shortcut.label ?? search.catalog.text.find((item) => item.field === shortcut.field)?.label;
  return <label className="relative min-w-32 flex-1 basis-40">
    <Glyph name="search" className="pointer-events-none absolute left-2 top-2 size-3.5 text-fg-muted" />
    <Toolbar.Input type="search" aria-label={labelText(label) ?? shortcut.field} placeholder={labelText(label) ?? shortcut.field}
      className="w-full pl-7" value={draft} onChange={(event) => { setDraft(event.currentTarget.value); commit(event.currentTarget.value); }} />
  </label>;
}

function FacetShortcut({ search, shortcut }: { search: ResourceSearch; shortcut: Extract<SearchShortcut, { kind: "facet" }> }) {
  const facet = search.catalog.facets.find((item) => item.field === shortcut.field)!;
  const label = shortcut.label ?? facet.label;
  const active = search.active.find((item) => item.kind === "facet" && item.field === shortcut.field);
  const activeOptions = active?.kind === "facet" ? active.options : [];
  const options = [...facet.options, ...activeOptions.filter((option) => !facet.options.some((known) => known.id === option.id))];
  const selected = activeOptions.map((option) => option.id);
  const multiple = shortcut.multiple !== false;
  const change = (value: string | string[] | null) => {
    const ids = typeof value === "string" ? [value] : value ?? [];
    search.setFacet(facet.field, ids);
  };
  return <SelectRoot<string, boolean> multiple={multiple} items={options.map((option) => ({ value: option.id, label: option.label }))}
    value={multiple ? selected : selected[0] ?? null} onValueChange={change}>
    <Toolbar.Button render={<SelectTrigger size="sm" aria-label={labelText(label) ?? facet.field} className="w-auto max-w-64" />}>
      <SelectValue>{() => activeOptions.length ? activeOptions.map((option, index) => <React.Fragment key={option.id}>
        {index > 0 ? ", " : null}{option.label}
      </React.Fragment>) : label}</SelectValue>
      <SelectIcon />
    </Toolbar.Button>
    <SelectPortal><SelectPositioner sideOffset={4}><SelectContent size="sm"><SelectList>
      {options.map((option) => <SelectItem key={option.id} value={option.id}><SelectItemText>{option.label}</SelectItemText><SelectItemIndicator /></SelectItem>)}
    </SelectList></SelectContent></SelectPositioner></SelectPortal>
  </SelectRoot>;
}

function ToggleShortcut({ search, id, label }: { search: ResourceSearch; id: string; label?: React.ReactNode }) {
  const filter = search.catalog.filters.find((item) => item.id === id);
  const favorite = search.catalog.favorites.find((item) => item.id === id);
  const pressed = search.active.some((item) => item.id === `${filter ? "filter" : "favorite"}:${id}`);
  return <Toolbar.Button render={<Toggle size="sm" pressed={pressed}
    onPressedChange={() => filter ? search.toggleFilter(id) : search.toggleFavorite(id)} />}>
    {label ?? filter?.label ?? favorite?.label}
  </Toolbar.Button>;
}

function GroupShortcut({ search }: { search: ResourceSearch }) {
  const t = useUiT();
  const labels = search.active.flatMap((item) => item.kind === "group" ? [groupLevelLabel(item, t)] : []);
  return <PopoverRoot><PopoverTrigger render={<Toolbar.Button />}>
    {t("resourceToolbar.groupBy")}{labels.length ? `: ${labels.join(" › ")}` : ""}<Glyph name="chevron-down" className="glyph" />
  </PopoverTrigger><PopoverPortal><PopoverPositioner sideOffset={4} align="start">
    <PopoverContent aria-label={t("resourceToolbar.groupBy")} className="w-80 max-w-[calc(100vw-1rem)] p-3"><GroupStackPanel search={search} /></PopoverContent>
  </PopoverPositioner></PopoverPortal></PopoverRoot>;
}

function ClauseShortcut({ search, shortcut }: { search: ResourceSearch; shortcut: Extract<SearchShortcut, { kind: "clause" }> }) {
  const t = useUiT();
  const [open, setOpen] = React.useState(false);
  const field = search.catalog.fields.find((item) => (item.field ?? item.id) === shortcut.field)!;
  const label = shortcut.label ?? field.label;
  const summary = search.active.filter((item) => item.kind === "clause" && parseCustomFilterId(item.id.slice("clause:".length))[0] === shortcut.field)
    .map((item) => labelText(item.label)).join(", ");
  const empty = Filter.from(search.filter).hasPreset({ [shortcut.field]: { isNull: true } });
  return <PopoverRoot open={open} onOpenChange={setOpen}>
    <PopoverTrigger aria-label={labelText(label) ?? shortcut.field} render={<Toolbar.Button />}>
      {summary || label}<Glyph name="chevron-down" className="glyph" />
    </PopoverTrigger><PopoverPortal><PopoverPositioner sideOffset={4} align="start">
      <PopoverContent aria-label={labelText(label) ?? shortcut.field} className="w-72 p-2">
        <ClauseEditor key={open ? "open" : "closed"} field={field} search={search} onApply={() => setOpen(false)} />
        {field.operators?.includes("isNull") ? <Toggle size="sm" pressed={empty} onPressedChange={(pressed) => {
          search.setClause(shortcut.field, pressed ? { field: shortcut.field, operator: "isNull" } : null);
        }}>{t("search.notGiven")}</Toggle> : null}
        <Button size="sm" variant="ghost" onClick={() => { search.setClause(shortcut.field, null); setOpen(false); }}>{t("resourceToolbar.clear")}</Button>
      </PopoverContent>
    </PopoverPositioner></PopoverPortal>
  </PopoverRoot>;
}

function ClauseEditor({ field, search, onApply }: { field: FilterClauseField; search: ResourceSearch; onApply: () => void }) {
  const t = useUiT();
  const [draft, setDraft] = React.useState<FilterClauseDraft>(() => {
    const filter = Filter.from(Filter.from(search.filter).onlyFields([field.field ?? field.id]));
    // An advanced Boolean predicate stays editable in the box's condition editor.
    let comparisons: ReturnType<Filter["conjunctions"]> = [];
    try { comparisons = filter.conjunctions(); } catch (error) {
      if (!(error instanceof QueryParseError)) throw error;
    }
    const comparison = comparisons[0];
    return { fieldId: field.id, operator: comparison?.operator === "isNull" && comparison.value === false ? "isNotNull" : comparison?.operator ?? "exact",
      value: comparison ? typeof comparison.value === "object" ? JSON.stringify(comparison.value) : String(comparison.value) : "" };
  });
  return <FilterClauseEditor fields={[field]} value={draft} onChange={setDraft} submitLabel={t("pager.apply")}
    onSubmit={(clause) => { search.setClause(field.field ?? field.id, clause); onApply(); }} />;
}
