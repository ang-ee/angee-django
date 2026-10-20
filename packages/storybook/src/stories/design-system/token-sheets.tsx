import type { CSSProperties, ReactNode } from "react";
import {
  Badge, Card, CardContent, Chip, FILLS, Glyph, Page, PageBody, PageHeader,
  SectionHeading, TONES, baseIcons, recipeAxes, textRoleVariants,
} from "@angee/ui";

import { tokenNames } from "@angee/ui/tokens";
import { noOwner, unresolvedClasses } from "./token-findings";

export function SheetPage({ title, description, children }: {
  title: string; description: string; children: ReactNode;
}) {
  return <Page className="min-h-screen bg-canvas text-fg">
    <PageHeader title={title} description={description} />
    <PageBody><div className="grid gap-6 p-4">{children}</div></PageBody>
  </Page>;
}

function tokenStyle(name: string): CSSProperties {
  const value = `var(${name})`;
  if (/^--animate-/.test(name)) return { animation: value, background: "var(--brand-soft)" };
  if (name === "--bg-grid-mask") return { maskImage: value, background: "var(--brand)" };
  if (name === "--bg-cover-wide-size") return { backgroundSize: value, backgroundImage: "linear-gradient(90deg, var(--brand-soft), var(--brand))" };
  if (name === "--bg-grid-step") return { backgroundSize: `${value} ${value}`, backgroundImage: "linear-gradient(var(--border-default) 1px, transparent 1px)" };
  if (/^--(?:font-family|font-(?:sans|mono))/.test(name)) return { fontFamily: value };
  if (/^--(?:fw-|font-weight-)/.test(name)) return { fontWeight: value };
  if (/^--lh-|--line-height$/.test(name)) return { lineHeight: value };
  if (/^--fs-|^--text-(?:\d|lg|2xs)/.test(name)) return { fontSize: value };
  if (/^--(?:r-|radius)/.test(name)) return { borderRadius: value, background: "var(--brand-soft)" };
  if (/^--(?:elevation|shadow)/.test(name)) return { boxShadow: value };
  if (/^--(?:sp-|spacing-|control-h|rail-w|topbar-h|breadcrumbbar-h|controlpanel-h)/.test(name)) {
    return { height: `min(${value}, 6rem)`, minHeight: "2px", width: "100%", background: "var(--brand-soft)" };
  }
  if (/^--(?:dur-|default-transition-duration)/.test(name)) return { transitionDuration: value };
  if (/^--(?:ease|default-transition-timing)/.test(name)) return { transitionTimingFunction: value };
  if (/^--z-index-/.test(name)) return { zIndex: value, position: "relative" };
  return { background: value, color: "var(--text-primary)" };
}

function TokenGrid({ names }: { names: readonly string[] }) {
  return <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
    {names.map(name => <Card key={name} data-token={name}>
      <CardContent className="grid gap-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <code className={textRoleVariants({ role: "caption" })}>{name}</code>
          {noOwner.has(name) && <Badge tone="warning">no owner</Badge>}
        </div>
        <div className="min-h-10 overflow-hidden rounded-4 border border-border p-2" style={tokenStyle(name)}>
          {/^(--font-|--fs-|--lh-|--fw-|--text-(?:\d|lg|2xs))/.test(name) ? "Aa · 0123456789" : null}
        </div>
        <code className={textRoleVariants({ role: "meta", className: "text-fg-muted" })}>var({name})</code>
      </CardContent>
    </Card>)}
  </div>;
}

export function TypeRoleMatrix() {
  return <div className="grid gap-4 sm:grid-cols-2">
      {recipeAxes(textRoleVariants).role.map(role => <Card key={role}><CardContent className="grid gap-2 p-4">
        <Chip>{role}</Chip>
        <p className={textRoleVariants({ role })}>A place for every detail · 123,456</p>
      </CardContent></Card>)}
  </div>;
}
export function TypeRolesSheet() {
  return <SheetPage title="Type roles" description="The shipped text recipe and CSS tokens. Change theme and color scheme in the toolbar.">
    <TypeRoleMatrix />
    <SectionHeading label="Font and type tokens" />
    <TokenGrid names={tokenNames.filter(name => /^--(?:font-|fs-|lh-|fw-|text-(?:\d|lg|2xs))/.test(name))} />
    <MissingClasses names={unresolvedClasses.filter(name => /^text-\d/.test(name))} />
  </SheetPage>;
}

export function ToneMatrix() {
  return <div className="grid gap-3">
      {[...TONES].sort().map(tone => <Card key={tone}><CardContent className="flex flex-wrap items-center gap-3 p-4">
        <code className={textRoleVariants({ role: "caption", className: "w-20" })}>{tone}</code>
        {[...FILLS].sort().map(variant => <Badge key={variant} tone={tone} variant={variant}>{variant}</Badge>)}
      </CardContent></Card>)}
  </div>;
}
export function TonesSheet() {
  return <SheetPage title="Tones and fills" description="Every semantic tone × fill, rendered by the shipped Badge. Unused tokens are inventory findings, not theme fallbacks.">
    <ToneMatrix />
    <TokenGrid names={tokenNames.filter(name =>
      /^--(?:color-|n-\d|b-\d|t-\d|black$|brand|accent|neutral|green|amber|red|blue|purple|pink|surface-|text-(?!\d|lg|2xs)|border-|success|warning|danger|info|on-|chart-|tooltip-|avatar-|ring|bg-grid-color)/.test(name))} />
    <MissingClasses names={unresolvedClasses.filter(name => !/^text-\d/.test(name))} />
  </SheetPage>;
}

function MissingClasses({ names }: { names: readonly string[] }) {
  return <Card><CardContent className="grid gap-3 p-4">
    <SectionHeading label="Unresolved utilities" />
    <p className={textRoleVariants({ role: "description" })}>These inventory class names have no token owner. Lane 2 must repair or delete their callers.</p>
    {names.map(name => <div key={name} className="flex items-center gap-3">
      <code>{name}</code><Badge tone="warning">no owner</Badge>
    </div>)}
  </CardContent></Card>;
}

export function GeometrySheet() {
  return <SheetPage title="Radius, spacing and control heights" description="Live dimensions, elevation and motion. The unused --sp-* scale remains visible beside the shipped control heights.">
    <TokenGrid names={tokenNames.filter(name =>
      /^--(?:r-|radius|sp-|spacing-|control-h|rail-w|topbar-h|breadcrumbbar-h|controlpanel-h|elevation-|shadow-|z-index-|dur-|ease|default-transition|animate-|bg-cover-wide-size|bg-grid-mask|bg-grid-step)/.test(name))} />
  </SheetPage>;
}

export function GlyphMatrix() {
  return <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
      {Object.keys(baseIcons).sort().map(name => <Card key={name}><CardContent className="flex flex-col items-center gap-3 p-4">
        <Glyph name={name} size="var(--control-h-md)" className="text-fg" />
        <code className={textRoleVariants({ role: "meta" })}>{name}</code>
      </CardContent></Card>)}
  </div>;
}
export function GlyphSheet() {
  return <SheetPage title="Glyphs" description="Registered base glyphs rendered by Glyph. Glyph names belong to the runtime registry; CSS controls their live foreground and dimensions.">
    <GlyphMatrix />
  </SheetPage>;
}
