import { useState, type ReactElement } from "react";
import { Alert, Button, Card, CardContent, Input, Label, useAppearance } from "@angee/ui";
import { useAuthoredQuery } from "@angee/refine";
import { AnalyseAppearance } from "./documents";

export function WebsiteAppearanceTool(): ReactElement {
  const appearance = useAppearance();
  const [url, setUrl] = useState("");
  const [submitted, setSubmitted] = useState("");
  const query = useAuthoredQuery(AnalyseAppearance, { url: submitted }, { enabled: Boolean(submitted) });
  const result = query.data?.analyseAppearance;
  async function analyse(): Promise<void> {
    if (!url.trim()) return;
    if (submitted === url.trim()) await query.refetch();
    else setSubmitted(url.trim());
  }
  async function useBrand(): Promise<void> {
    const brand = result?.colors[0];
    if (!brand) return;
    const accent = result.colors[1] ?? brand;
    await appearance.setTheme("angee.brand", { version: 1, value: { brand, accent, radius: "6px" } });
  }
  return <Card><CardContent className="grid gap-4">
    <div className="grid gap-1"><Label htmlFor="appearance-website">Website URL</Label><div className="flex gap-2"><Input id="appearance-website" type="url" value={url} placeholder="https://example.com" onChange={(event) => setUrl(event.target.value)} /><Button loading={query.isFetching} onClick={() => void analyse()}>Analyse</Button></div></div>
    {query.error ? <Alert tone="danger" title="Website analysis failed">{query.error.message}</Alert> : null}
    {result ? <div className="grid gap-3"><div><strong>{result.siteName || result.title || result.finalUrl}</strong><div className="text-xs text-fg-muted">{result.finalUrl}</div></div><div className="flex flex-wrap gap-2">{result.colors.map((color) => <span key={color} className="inline-flex items-center gap-1 text-xs"><span className="size-5 rounded-full border border-border" style={{ backgroundColor: color }} />{color}</span>)}</div>{result.neutralTint ? <div className="text-xs text-fg-muted">Neutral tint: {result.neutralTint}</div> : null}{result.fonts.length ? <div className="text-xs text-fg-muted">Fonts: {result.fonts.join(", ")}</div> : null}{result.warnings.map((warning) => <Alert key={warning} tone="warning" title={warning} />)}<Button disabled={!result.colors.length} onClick={() => void useBrand()}>Use in Brand theme</Button></div> : null}
  </CardContent></Card>;
}
