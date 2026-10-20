import { useEffect, useRef, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, MetaGrid,
  ModalsHost, SearchInput, SectionHeading, Skeleton, textRoleVariants, useAppearance,
} from "@angee/ui";
import { COMPONENT_KINDS, componentCatalog, type CatalogEntry } from "@angee/ui/catalog";
import { useDebouncedText } from "@angee/ui/lib/use-debounced-text";
import { specimens } from "./design-system/specimens";
import { SheetPage } from "./design-system/token-sheets";
import { observeSpecimen } from "./design-system/observe-specimen";
import { themeGlobalValue } from "../theme-global";

function LiveSpecimen({ entry, globals }: { entry: CatalogEntry; globals: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!visible && host.current) return observeSpecimen(host.current, () => setVisible(true));
  }, [visible]);
  const Specimen = specimens[entry.name as keyof typeof specimens];
  return <div ref={host} className="min-h-80">
    {!visible ? <Skeleton className="h-80 w-full" /> : Specimen ? <Specimen /> : entry.storyId ? <iframe title={`${entry.name} shipped specimen`}
      src={`/iframe.html?id=${entry.storyId}&viewMode=story&globals=${encodeURIComponent(globals)}`}
      className="h-80 w-full rounded-6 border border-border-subtle" />
      : <EmptyState title="No specimen" description="Coverage gap: this export has no registered specimen or owner story." />}
  </div>;
}

function ComponentCard({ entry, globals }: { entry: CatalogEntry; globals: string }) {
  const href = entry.storyId ? `/?path=/story/${entry.storyId}` : undefined;
  return <Card id={`${entry.package.slice(7)}-${entry.name}`} data-catalog-export={`${entry.package}:${entry.name}`}>
    <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
      <CardTitle>{entry.name}</CardTitle>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={entry.status === "stable" ? "neutral" : "warning"}>{entry.status}</Badge>
        {href && <Button asChild size="sm" variant="link"><a href={href} target="_top">Owner story ↗</a></Button>}
      </div>
    </CardHeader>
    <CardContent className="grid gap-3">
      <p className={textRoleVariants({ role: "caption" })}>{entry.package}{entry.region ? ` · ${entry.region}` : ""}</p>
      <LiveSpecimen entry={entry} globals={globals} />
      {entry.owner && <p className={textRoleVariants({ role: "caption" })}>Follows {entry.owner}</p>}
    </CardContent>
  </Card>;
}

function OverviewPage() {
  const [search, setSearch] = useState("");
  const { draft, setDraft, commit } = useDebouncedText(search, setSearch);
  const { effectiveThemeId, colorScheme } = useAppearance();
  const globals = `themeId:${themeGlobalValue(effectiveThemeId ?? "angee.stock")};colorScheme:${colorScheme}`;
  const matches = componentCatalog.filter(entry => entry.kind && `${entry.name} ${entry.package} ${entry.kind}`.toLowerCase().includes(search.toLowerCase()));
  const unclassified = componentCatalog.filter(entry => !entry.kind);
  return <ModalsHost><SheetPage title="Angee library" description="The shipped owner is the authority. Explore the seven kinds, live recipe matrices and the stories that render each owner.">
    <SearchInput aria-label="Find a library export" placeholder="Search components, hooks or kinds…" value={draft}
      onChange={event => { setDraft(event.target.value); commit(event.target.value); }} />
    <nav aria-label="Component kinds" className="flex flex-wrap gap-2">{COMPONENT_KINDS.map(kind =>
      <Button key={kind} asChild size="sm"><a href={`#kind-${kind}`}>{kind} · {matches.filter(e=>e.kind===kind).length}</a></Button>)}</nav>
    {COMPONENT_KINDS.map(kind => {
      const entries = matches.filter(entry => entry.kind === kind);
      return <section key={kind} id={`kind-${kind}`} className="grid gap-4">
        <SectionHeading label={kind} count={entries.length} />
        {entries.length ? entries.map(entry => <ComponentCard key={`${entry.package}:${entry.name}`} entry={entry} globals={globals} />)
          : <EmptyState title={`No ${kind} matches`} description="Try a different export name." />}
      </section>;
    })}
    <section className="grid gap-3"><SectionHeading label="Outside the UX ontology" count={unclassified.length} />
      <p className={textRoleVariants({ role: "description" })}>Runtime, transport, schema and provider infrastructure remains in the export audit with an explicit reason.</p>
      <MetaGrid rows={unclassified.filter(entry => entry.name.toLowerCase().includes(search.toLowerCase()))
        .map(entry => [`${entry.package}:${entry.name}`, entry.reason])} />
    </section>
  </SheetPage></ModalsHost>;
}
const meta = { title: "Library/Overview", component: OverviewPage, parameters: { layout: "fullscreen" } } satisfies Meta<typeof OverviewPage>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
