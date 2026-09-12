import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
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
  return <div className="grid h-full grid-cols-[4.5rem_minmax(0,1fr)] overflow-hidden rounded-6 border border-border bg-sheet shadow-sm">
    <aside className="grid content-start gap-1 bg-rail p-2 text-on-rail">
      <ThemeLogo logo={logo} size={16} width={16} height={16} />
      <span className="mt-1 h-1.5 rounded-full bg-rail-hi" />
      <span className="h-1.5 w-4/5 rounded-full bg-rail-hi" />
    </aside>
    <section className="grid content-start gap-2 p-2">
      <span className="h-2 w-2/3 rounded-full bg-fg-muted opacity-40" />
      <span className="h-4 rounded-4 bg-brand" />
      <span className="grid grid-cols-2 gap-1"><i className="h-7 rounded-4 border border-border bg-canvas" /><i className="h-7 rounded-4 border border-border bg-sheet-2" /></span>
    </section>
  </div>;
}

/** Shared controls and data surfaces used to review every installed theme. */
export function ThemeSpecimenSurface({ logo }: { logo?: ThemeCustomizationLogo } = {}): ReactNode {
  return <div className="grid gap-5 lg:grid-cols-[10rem_minmax(0,1fr)]">
    <aside className="grid content-start gap-2 rounded-8 border border-border-on-rail bg-rail p-3 text-on-rail">
      <div className="mb-2 flex items-center gap-2 text-13 font-semibold text-on-rail-hi">
        <ThemeLogo logo={logo} size={20} width={20} height={20} />
        Workspace
      </div>
      {["Overview", "Projects", "Reports"].map((label, index) => <div key={label} className={index === 0 ? "rounded-6 bg-rail-hi px-2 py-1.5 text-12 text-on-rail-hi" : "rounded-6 px-2 py-1.5 text-12 text-on-rail-mut"}>{label}</div>)}
    </aside>
    <div className="grid gap-5">
    <div className="grid gap-1"><span className="text-11 font-semibold uppercase tracking-wide text-fg-muted">Theme specimen</span><h2 className="text-22 font-semibold">Workspace overview</h2><p className="text-13 text-fg-muted">Typography, controls, status, fields and tabular surfaces use the active token contract.</p></div>
    <div className="flex flex-wrap items-center gap-2"><Button>Primary action</Button><Button variant="secondary">Secondary</Button><Button variant="ghost">Quiet action</Button><Badge tone="success">On track</Badge><Badge tone="warning">Needs review</Badge></div>
    <Card><CardHeader><CardTitle>Project details</CardTitle><CardDescription>Interactive fields remain inside this preview document.</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2"><Label>Project name<Input defaultValue="Northstar" /></Label><Label>Owner<Input defaultValue="Alex Morgan" /></Label><Checkbox defaultChecked>Send a weekly summary</Checkbox></CardContent></Card>
    <Table><TableHeader><TableRow><TableHead>Item</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Value</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell>Design review</TableCell><TableCell><Badge tone="info">Active</Badge></TableCell><TableCell className="text-right">72%</TableCell></TableRow><TableRow><TableCell>Release prep</TableCell><TableCell><Badge>Queued</Badge></TableCell><TableCell className="text-right">18%</TableCell></TableRow></TableBody></Table>
    </div>
  </div>;
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
