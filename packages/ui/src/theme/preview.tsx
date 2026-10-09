import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { barVariants } from "../layouts/bar";
import { cn } from "../lib/cn";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { applyAppearanceRoot } from "./appearance";
import { ThemeLogo } from "./logo";
import type { ColorScheme, ThemeCustomizationLogo, ThemeTokenName } from "./runtime.mjs";

export interface ThemePreviewFrameProps {
  title: string;
  themeId: string | null;
  colorScheme: ColorScheme;
  tokens?: Partial<Record<ThemeTokenName, string>>;
  logo?: ThemeCustomizationLogo;
  children?: ReactNode;
  className?: string;
  variant?: "full" | "card";
}

/** Isolated preview document using the host's compiled presentation assets. */
export function ThemePreviewFrame({ title, themeId, colorScheme, tokens, logo, children, className, variant = "full" }: ThemePreviewFrameProps): ReactNode {
  const frame = useRef<HTMLIFrameElement>(null);
  const [target, setTarget] = useState<Document | null>(null);

  useEffect(() => {
    if (!target) return;
    copyPresentationHead(document, target);
    applyAppearanceRoot({ themeId, colorScheme, tokens, target });
  }, [colorScheme, target, themeId, tokens]);

  return <>
    <iframe
      ref={frame}
      title={title}
      sandbox="allow-same-origin"
      srcDoc="<!doctype html><html><head></head><body></body></html>"
      className={className ?? (variant === "card" ? "h-24 w-full rounded-6 border border-border bg-canvas" : "h-[30rem] w-full rounded-8 border border-border bg-canvas")}
      onLoad={() => setTarget(frame.current?.contentDocument ?? null)}
    />
    {target ? createPortal(
      <main className={variant === "card" ? "h-screen overflow-hidden bg-canvas p-2 text-fg" : "min-h-screen bg-canvas p-5 text-fg"}>
        {variant === "card" ? <ThemeCardSpecimen logo={logo} /> : <ThemeSpecimenSurface logo={logo} />}
        {children}
      </main>,
      target.body,
    ) : null}
  </>;
}

function ThemeCardSpecimen({ logo }: { logo?: ThemeCustomizationLogo }): ReactNode {
  return <ThemeSpecimenChrome compact logo={logo} controls={<>
    <span className="h-1.5 w-5 rounded-2 bg-brand" />
    <span className="h-1.5 w-4 rounded-2 border border-border bg-sheet-2" />
  </>}>
    <span className="h-1.5 w-2/3 rounded-full bg-fg-muted opacity-40" />
    <span className="grid grid-cols-2 gap-1"><i className="h-5 rounded-4 border border-border bg-sheet" /><i className="h-5 rounded-4 border border-border bg-sheet-2" /></span>
  </ThemeSpecimenChrome>;
}

/** Static L-shaped chrome; sample copy stays literal in the isolated preview. */
function ThemeSpecimenChrome({ compact = false, logo, controls, children }: {
  compact?: boolean;
  logo?: ThemeCustomizationLogo;
  controls: ReactNode;
  children: ReactNode;
}): ReactNode {
  return <div className={cn("flex overflow-hidden border border-border bg-canvas text-fg", compact ? "h-full rounded-6 shadow-sm" : "rounded-8")}>
    <aside aria-label="App rail" className={cn("flex shrink-0 flex-col items-center border-r border-border-on-rail bg-rail text-on-rail", compact ? "w-7 gap-1 p-1" : "w-rail-w gap-2 py-2")}>
      <ThemeLogo logo={logo} role="img" aria-label="Logo" size={compact ? 16 : 20} width={compact ? 16 : 20} height={compact ? 16 : 20} />
      {["Workspace", "Projects", "Reports"].map((label, index) => <span key={label} role="img" aria-label={`${label} app`} aria-current={index === 0 ? "page" : undefined}
        className={cn("relative grid shrink-0 place-items-center rounded-4", compact ? "size-3" : "size-9", index === 0 ? "bg-rail-hi text-on-rail-hi" : "text-on-rail-mut")}>
        {index === 0 ? <i aria-hidden="true" className="absolute -left-1 h-2/3 w-0.5 rounded-r-2 bg-brand" /> : null}
        <span aria-hidden="true" className={compact ? "size-1.5 rounded-2 bg-current" : "size-4 rounded-4 border-2 border-current"} />
      </span>)}
    </aside>
    <div className="flex min-w-0 flex-1 flex-col">
      <header role="banner" aria-label="Top bar" className={cn(barVariants({ height: compact ? "none" : "topbar", edge: "bottom", tone: "rail", gap: 2 }), compact ? "h-4 gap-1 px-1" : "px-3")}>
        <span className={compact ? "h-1 w-6 shrink-0 rounded-full bg-on-rail-hi" : "shrink-0 text-15 font-semibold text-on-rail-hi"}>{compact ? null : "Workspace"}</span>
        <span aria-hidden="true" className={cn("w-px shrink-0 bg-on-rail-mut/40", compact ? "h-2" : "h-4")} />
        <nav aria-label="Workspace menu" className="flex h-full min-w-0 items-center gap-1">
          {["Overview", "Projects", "Reports"].map((label, index) => <span key={label} aria-current={index === 0 ? "page" : undefined}
            className={cn("flex h-full shrink-0 items-center border-b-2", compact ? "px-0.5" : "px-2 text-13", index === 0 ? "border-brand text-on-rail-hi" : "border-transparent text-on-rail-mut")}>
            {compact ? <i className="h-1 w-3 rounded-full bg-current" /> : label}
          </span>)}
        </nav>
      </header>
      <div role="group" aria-label="Control band" className={cn(barVariants({ height: compact ? "none" : "controlMin", edge: "bottom", tone: "sheet", pad: compact ? "none" : "comfortable", gap: 2 }), compact ? "h-3 gap-1 px-1" : "flex-wrap")}>
        {controls}
      </div>
      <section aria-label="Page samples" className={cn("grid content-start", compact ? "gap-1 p-1" : "gap-5 p-4")}>
        {children}
      </section>
    </div>
  </div>;
}

/** Shared controls and data surfaces used to review every installed theme. */
export function ThemeSpecimenSurface({ logo }: { logo?: ThemeCustomizationLogo } = {}): ReactNode {
  return <ThemeSpecimenChrome logo={logo} controls={<><Button variant="primary">Primary action</Button><Button variant="secondary">Secondary</Button></>}>
    <div className="grid gap-1"><SectionEyebrow as="span">Theme specimen</SectionEyebrow><h2 className="text-22 font-semibold">Workspace overview</h2><p className="text-13 text-fg-muted">Typography, controls, status, fields and tabular surfaces use the active token contract.</p></div>
    <div className="flex flex-wrap items-center gap-2"><Button variant="ghost">Quiet action</Button><Badge tone="success">On track</Badge><Badge tone="warning">Needs review</Badge></div>
    <Card><CardHeader><CardTitle>Project details</CardTitle><CardDescription>Interactive fields remain inside this preview document.</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2"><Label>Project name<Input defaultValue="Northstar" /></Label><Label>Owner<Input defaultValue="Alex Morgan" /></Label><Checkbox defaultChecked>Send a weekly summary</Checkbox></CardContent></Card>
    <Table><TableHeader><TableRow><TableHead>Item</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Value</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell>Design review</TableCell><TableCell><Badge tone="info">Active</Badge></TableCell><TableCell className="text-right">72%</TableCell></TableRow><TableRow><TableCell>Release prep</TableCell><TableCell><Badge>Queued</Badge></TableCell><TableCell className="text-right">18%</TableCell></TableRow></TableBody></Table>
  </ThemeSpecimenChrome>;
}

function copyPresentationHead(source: Document, target: Document): void {
  for (const node of target.head.querySelectorAll("[data-angee-preview-style]")) node.remove();
  const base = target.createElement("base");
  base.href = source.baseURI;
  base.dataset.angeePreviewStyle = "";
  target.head.prepend(base);
  for (const node of source.head.querySelectorAll('link[rel="stylesheet"], style')) {
    const copy = node.cloneNode(true) as HTMLElement;
    copy.dataset.angeePreviewStyle = "";
    target.head.append(copy);
  }
}
