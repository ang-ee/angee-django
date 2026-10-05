import * as React from "react";
import type { GroupSpec } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../../i18n";
import { Button } from "../../../ui/button";
import { Select } from "../../../ui/select";
import type { ResourceToolbarGroupOption } from "../../../toolbars/ResourceToolbar";
import { serializeResourceViewGroup } from "../model/search";
import { resourceViewGroupsEqual } from "../resource-view-model";
import { labelText } from "../resource-view-utils";
import type { ResourceSearch, SearchActiveItem } from "./types";

type GroupItem = Extract<SearchActiveItem, { kind: "group" }>;
const PRIMARY_GRANULARITIES = new Set(["year", "quarter", "month", "week", "day"]);

/** Shared text for level actions, chips and the group trigger. Activity owns the axis label. */
export function groupLevelLabel(item: GroupItem, t: UiTranslate): string {
  const label = labelText(item.label) ?? item.level.field;
  return item.level.granularity ? `${label} · ${t(`search.granularity.${item.level.granularity}`)}` : label;
}

/** Preserve the catalog's rich label in both the level row and its chip. */
export function GroupLevelLabel({ item }: { item: GroupItem }): React.ReactElement {
  const t = useUiT();
  return <>{item.label}{item.level.granularity ? <> · {t(`search.granularity.${item.level.granularity}`)}</> : null}</>;
}

/** Both group hosts render this projection; every edit goes through the search commands. */
export function GroupStackPanel({ search }: { search: ResourceSearch }): React.ReactElement {
  const t = useUiT();
  const [moreOpen, setMoreOpen] = React.useState(false);
  const [axisId, setAxisId] = React.useState("");
  const [granularityDraft, setGranularityDraft] = React.useState("");
  const { catalog, groupStack } = search;
  const levels = search.active.filter((item): item is GroupItem => item.kind === "group");
  const canAdd = search.groupingEnabled && (search.maxGroupDepth === undefined || groupStack.length < search.maxGroupDepth);
  const selected = catalog.groups.find((axis) => axis.id === axisId) ?? catalog.groups[0];
  const granularity = selected?.granularities?.includes(granularityDraft)
    ? granularityDraft : selected?.group.granularity ?? selected?.granularities?.[0];
  const selectedLevel = selected && (granularity ? { ...selected.group, granularity } : selected.group);
  const contains = (level: GroupSpec) => groupStack.some((current) => resourceViewGroupsEqual(current, level));

  return <div className="grid gap-3">
    <section>
      <h3 className="mb-2 px-2 text-13 font-semibold">{t("search.levels")}</h3>
      <ol aria-label={t("search.levels")} className="grid gap-1">
        {levels.map((item) => <GroupLevel key={serializeResourceViewGroup(item.level)} item={item}
          search={search} axis={catalog.groups.find((axis) => axis.group.field === item.level.field)} />)}
      </ol>
      {levels.length > 0 ? <Button type="button" variant="ghost" size="sm"
        onClick={() => search.setGroupStack([])}>{t("search.clearGrouping")}</Button> : null}
    </section>
    <section className="grid gap-1 border-t border-border-subtle pt-2">
      <h3 className="mb-1 px-2 text-13 font-semibold">{t("search.addLevel")}</h3>
      {catalog.curatedGroups.map((option) => {
        const axis = catalog.groups.find((axis) => axis.group.field === option.group.field);
        return <GroupAxisChoice key={option.id} option={option} axis={axis} canAdd={canAdd}
          contains={contains} onAdd={search.addGroup} />;
      })}
      <Button type="button" variant="ghost" size="sm" className="justify-start"
        disabled={!canAdd} aria-expanded={moreOpen} onClick={() => setMoreOpen((open) => !open)}>
        <Glyph name="plus" className="size-3" />{t("search.moreAxes")}
      </Button>
      {moreOpen ? <div className="grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2">
        <Select size="sm" value={selected?.id ?? ""} aria-label={t("search.groupAxis")}
          disabled={!canAdd || !selected} options={catalog.groups.map((axis) => ({ value: axis.id, label: axis.label }))}
          onValueChange={setAxisId} />
        {selected?.granularities?.length ? <Select size="sm" value={granularity}
          disabled={!canAdd} aria-label={t("search.groupGranularity")}
          options={granularityOptions(selected, t)}
          onValueChange={setGranularityDraft} /> : null}
        <Button type="button" variant="secondary" size="sm" className="justify-center"
          disabled={!canAdd || !selectedLevel || contains(selectedLevel)} onClick={() => {
            if (selectedLevel) search.addGroup(selectedLevel);
          }}>{t("search.addLevel")}</Button>
      </div> : null}
    </section>
  </div>;
}

function GroupLevel({ item, search, axis }: {
  item: GroupItem;
  search: ResourceSearch;
  axis: ResourceToolbarGroupOption | undefined;
}): React.ReactElement {
  const t = useUiT();
  const rowRef = React.useRef<HTMLLIElement>(null);
  const focusAfterMove = React.useRef(false);
  // A level's stable key follows it through a reorder. The clicked move button
  // may disappear at an end, so restore focus to that same level after the edit.
  React.useLayoutEffect(() => {
    if (focusAfterMove.current) {
      rowRef.current?.focus();
      focusAfterMove.current = false;
    }
  }, [item.index]);
  const label = groupLevelLabel(item, t);
  const move = (to: number) => {
    focusAfterMove.current = true;
    search.moveGroup(item.index, to);
  };
  return <li ref={rowRef} tabIndex={-1} aria-label={label}
    className="grid gap-1 rounded-6 bg-inset p-2 outline-none focus-visible:focus-ring">
    <div className="flex min-w-0 items-center gap-1">
      <span className="min-w-0 flex-1 text-13"><GroupLevelLabel item={item} /></span>
      {item.index > 0 ? <Button type="button" variant="ghost" size="iconSm"
        disabled={!search.groupingEnabled} aria-label={t("search.moveGroupUp", { label })}
        onClick={() => move(item.index - 1)}><Glyph name="arrow-up" /></Button> : null}
      {item.index < search.groupStack.length - 1 ? <Button type="button" variant="ghost" size="iconSm"
        disabled={!search.groupingEnabled} aria-label={t("search.moveGroupDown", { label })}
        onClick={() => move(item.index + 1)}><Glyph name="arrow-down" /></Button> : null}
      <Button type="button" variant="ghost" size="iconSm" disabled={!search.groupingEnabled}
        aria-label={t("search.removeGroup", { label })} onClick={() => search.removeGroup(item.index)}>
        <Glyph name="x" /></Button>
    </div>
    {axis?.granularities?.length ? <Select size="sm" value={item.level.granularity ?? ""}
      placeholder={t("search.groupGranularity")} disabled={!search.groupingEnabled}
      aria-label={t("search.levelGranularity", { label })}
      options={granularityOptions(axis, t)}
      onValueChange={(granularity) => search.setGroupLevel(item.index, { ...item.level, granularity })} /> : null}
  </li>;
}

function GroupAxisChoice({ option, axis, canAdd, contains, onAdd }: {
  option: ResourceToolbarGroupOption;
  axis: ResourceToolbarGroupOption | undefined;
  canAdd: boolean;
  contains: (level: GroupSpec) => boolean;
  onAdd: ResourceSearch["addGroup"];
}): React.ReactElement {
  const t = useUiT();
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const granularities = axis?.granularities ?? option.granularities ?? [];
  const { ranges, parts } = granularityFamilies(axis ?? option);
  const level = option.group.granularity ? option.group : axis?.group ?? option.group;
  const visible = advancedOpen ? ranges : ranges.filter((value) => PRIMARY_GRANULARITIES.has(value));
  const choice = (granularity: string) => {
    const level = { ...option.group, granularity };
    return <Button key={granularity} type="button" variant="ghost" size="sm" className="h-5 px-1.5 text-2xs"
      disabled={!canAdd || contains(level)}
      aria-label={t("search.addGroupGranularity", { label: labelText(option.label) ?? option.group.field,
        granularity: t(`search.granularity.${granularity}`) })}
      onClick={() => onAdd(level)}>{t(`search.granularity.${granularity}`)}</Button>;
  };
  return <div>
    <Button type="button" variant="ghost" size="sm" className="w-full justify-start"
      disabled={!canAdd || contains(level)} onClick={() => onAdd(level)}>{option.label}</Button>
    {granularities.length > 0 ? <div className="flex flex-wrap gap-0.5 px-2">
      <div role="group" aria-label={t("search.ranges")} className="flex flex-wrap gap-0.5">{visible.map(choice)}</div>
      {advancedOpen && parts.length ? <section aria-label={t("search.numberParts")} className="w-full border-t border-border-subtle pt-1">
        <h4 className="mb-1 text-2xs text-fg-muted">{t("search.numberParts")}</h4>
        <div className="flex flex-wrap gap-0.5">{parts.map(choice)}</div>
      </section> : null}
      {parts.length > 0 || ranges.some((value) => !PRIMARY_GRANULARITIES.has(value)) ? <Button type="button" variant="ghost"
        size="sm" className="h-5 px-1.5 text-2xs" aria-expanded={advancedOpen}
        aria-label={t(advancedOpen ? "search.lessGranularities" : "search.moreGranularities",
          { label: labelText(option.label) ?? option.group.field })}
        onClick={() => setAdvancedOpen((open) => !open)}>
        {t(advancedOpen ? "resourceToolbar.basicGranularity" : "resourceToolbar.advancedGranularity")}</Button> : null}
    </div> : null}
  </div>;
}

/** Extraction metadata decides the two families, preserving order within each. */
function granularityOptions(axis: ResourceToolbarGroupOption, t: UiTranslate) {
  const { ranges, parts } = granularityFamilies(axis);
  return [...ranges.map((value) => ({ value, label: t(`search.granularity.${value}`), group: t("search.ranges") })),
    ...parts.map((value) => ({ value, label: t(`search.granularity.${value}`), group: t("search.numberParts") }))];
}

function granularityFamilies(axis: ResourceToolbarGroupOption) {
  const values = axis.granularities ?? [];
  const drills = axis.granularityDrills;
  const ranges = values.filter((value) => drills === undefined || drills.includes(value));
  const parts = values.filter((value) => drills !== undefined && !drills.includes(value));
  return { ranges, parts };
}
