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
import type { ColorScheme, ThemeTokenName } from "./runtime.mjs";

export interface ThemePreviewFrameProps {
  title: string;
  themeId: string | null;
  colorScheme: ColorScheme;
  tokens?: Partial<Record<ThemeTokenName, string>>;
  children: ReactNode;
  className?: string;
}

/** Isolated preview document using the host's compiled presentation assets. */
export function ThemePreviewFrame({ title, themeId, colorScheme, tokens, children, className }: ThemePreviewFrameProps): ReactNode {
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
      className={className ?? "h-[30rem] w-full rounded-8 border border-border bg-canvas"}
      onLoad={() => setTarget(frame.current?.contentDocument ?? null)}
    />
    {target ? createPortal(<main className="min-h-screen bg-canvas p-5 text-fg"><ThemeSpecimenSurface />{children}</main>, target.body) : null}
  </>;
}

/** Shared controls and data surfaces used to review every installed theme. */
export function ThemeSpecimenSurface(): ReactNode {
  return <div className="grid gap-5">
    <div className="grid gap-1"><span className="text-11 font-semibold uppercase tracking-wide text-fg-muted">Theme specimen</span><h2 className="text-22 font-semibold">Workspace overview</h2><p className="text-13 text-fg-muted">Typography, controls, status, fields and tabular surfaces use the active token contract.</p></div>
    <div className="flex flex-wrap items-center gap-2"><Button>Primary action</Button><Button variant="secondary">Secondary</Button><Button variant="ghost">Quiet action</Button><Badge tone="success">On track</Badge><Badge tone="warning">Needs review</Badge></div>
    <Card><CardHeader><CardTitle>Project details</CardTitle><CardDescription>Interactive fields remain inside this preview document.</CardDescription></CardHeader><CardContent className="grid gap-4 sm:grid-cols-2"><Label>Project name<Input defaultValue="Northstar" /></Label><Label>Owner<Input defaultValue="Alex Morgan" /></Label><Checkbox defaultChecked>Send a weekly summary</Checkbox></CardContent></Card>
    <Table><TableHeader><TableRow><TableHead>Item</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Value</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell>Design review</TableCell><TableCell><Badge tone="info">Active</Badge></TableCell><TableCell className="text-right">72%</TableCell></TableRow><TableRow><TableCell>Release prep</TableCell><TableCell><Badge>Queued</Badge></TableCell><TableCell className="text-right">18%</TableCell></TableRow></TableBody></Table>
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
