import { Badge, Card, CardContent, CardHeader, CardTitle } from "@angee/base";
import { useT } from "@angee/sdk";
import type { ReactNode } from "react";

import { useOperatorSnapshot } from "../../data/transport";
import type { TemplateDescriptor, TemplateInputDescriptor } from "../../data/types";
import { SectionEmpty, SectionError, SectionLoading } from "../parts/SectionStatus";

/** Templates pane: the addable template catalog + each template's input schema. */
export function TemplatesSection(): ReactNode {
  const t = useT("operator");
  const { snapshot, result } = useOperatorSnapshot({ templates: true });

  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }
  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading templates" />;
  }

  const templates = snapshot?.templates ?? [];

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.templates.title")}</h2>

      {templates.length === 0 ? (
        <SectionEmpty message="No templates." />
      ) : (
        <div className="flex flex-col gap-3">
          {templates.map((template) => (
            <TemplateCard key={template.ref} template={template} />
          ))}
        </div>
      )}
    </div>
  );
}

function TemplateCard({ template }: { template: TemplateDescriptor }): ReactNode {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {template.name ?? template.ref}
          <span className="font-mono text-13 font-normal text-fg-muted">{template.ref}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-13 text-fg-muted">
          <Badge density="compact" shape="pill" variant="default">
            {template.kind}
          </Badge>
          <span className="font-mono">{template.path}</span>
        </div>

        {template.inputs.length > 0 ? (
          <div className="flex flex-col gap-1">
            <span className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
              Inputs
            </span>
            {template.inputs.map((input) => (
              <TemplateInputRow input={input} key={input.name} />
            ))}
          </div>
        ) : null}

        {/* TODO(S6): render a template into a workspace/service (workspaceCreate)
            once an input-collection form lands. */}
      </CardContent>
    </Card>
  );
}

function TemplateInputRow({ input }: { input: TemplateInputDescriptor }): ReactNode {
  return (
    <div className="flex flex-wrap items-center gap-2 text-13">
      <span className="font-mono text-fg-2">{input.name}</span>
      <Badge density="compact" shape="pill" variant="default">
        {input.type ?? "—"}
      </Badge>
      <Badge density="compact" shape="pill" variant={input.required ? "warning" : "default"}>
        {input.required ? "required" : "optional"}
      </Badge>
      {input.immutable ? (
        <Badge density="compact" shape="pill" variant="default">
          immutable
        </Badge>
      ) : null}
      {input.generated ? (
        <Badge density="compact" shape="pill" variant="default">
          generated
        </Badge>
      ) : null}
      <span className="text-fg-muted">= {input.default ?? "—"}</span>
    </div>
  );
}
