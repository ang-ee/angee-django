import { useAuthoredQuery } from "@angee/refine";
import { Button, Checkbox, Glyph, Input, PopoverRoot, PopoverTrigger, PopoverPortal, PopoverPositioner, PopoverContent, PopoverTitle, SectionEyebrow, Select, Tag } from "@angee/ui";
import { InboxAccounts } from "./documents";
import { useNexusT } from "../i18n";
import { INBOX_MODELS, useInboxFilter } from "./state";

/** Coverage and local message predicates share one native collection filter. */
export function InboxFilters() {
  const t = useNexusT();
  const view = useInboxFilter();
  const sources = useAuthoredQuery(InboxAccounts, {}, { models: INBOX_MODELS });
  const facets = [
    { field: "platform", options: (sources.data?.inbox_platforms ?? []).map(value => ({ value, label: value })) },
    { field: "account", options: (sources.data?.inbox_accounts ?? []).map(row => ({ value: row.id, label: row.display_name })) },
    { field: "kind", options: ["mail", "direct", "group", "other"].map(value => ({ value, label: t(`inbox.${value}`) })) },
  ];
  return <PopoverRoot><PopoverTrigger render={<Button size="sm" variant="secondary" />}><Glyph name="filter" />{t("inbox.filters")}
    {view.filter.hasEntries() ? <Tag>{Object.keys(view.state.filter).length}</Tag> : null}</PopoverTrigger>
    <PopoverPortal><PopoverPositioner sideOffset={8} align="end"><PopoverContent className="max-h-[80vh] w-80 overflow-auto">
      <PopoverTitle>{t("inbox.coverage")}</PopoverTitle><div className="space-y-4 p-4">
        {facets.map(facet => <fieldset key={facet.field} className="space-y-1.5"><legend className="mb-2 text-13 font-medium">{t(`inbox.${facet.field}`)}</legend>
          {facet.options.map(option => <label key={option.value} className="flex items-center gap-2 text-13"><Checkbox size="sm"
            checked={view.filter.facetValues(facet.field).includes(option.value)} onCheckedChange={() => view.toggle(facet.field, option.value)} />{option.label}</label>)}
        </fieldset>)}
        <div className="grid grid-cols-2 gap-2">{["after", "before"].map(field => <label key={field} className="text-13">{t(`inbox.${field}`)}<Input type="date" size="sm"
          value={view.filter.facetValues(field)[0]?.slice(0, 10) ?? ""} onChange={event => view.setValue(field, event.target.value ? `${event.target.value}T00:00:00Z` : "")} /></label>)}</div>
        <SectionEyebrow>{t("inbox.message")}</SectionEyebrow>
        {[{ field: "attachment", values: ["any", "image", "video", "audio", "document", "none"] }, { field: "direction", values: ["inbound", "outbound"] }].map(({ field, values }) => <label key={field} className="block space-y-1 text-13">{t(`inbox.${field}`)}<Select size="sm" aria-label={t(`inbox.${field}`)} value={view.filter.facetValues(field)[0] ?? ""}
          onValueChange={value => view.setValue(field, value)} options={[{ value: "", label: t("inbox.all") }, ...values.map(value => ({ value, label: t(`inbox.${value}`) }))]} /></label>)}
        {["quoted", "starred"].map(field => <label key={field} className="flex items-center gap-2 text-13"><Checkbox size="sm" checked={view.filter.facetValues(field).includes("yes")} onCheckedChange={value => view.setValue(field, value ? "yes" : "")} />{t(`inbox.${field}`)}</label>)}
        <Button variant="ghost" size="sm" onClick={() => view.setFilter({})}>{t("inbox.clear")}</Button>
      </div></PopoverContent></PopoverPositioner></PopoverPortal>
  </PopoverRoot>;
}
