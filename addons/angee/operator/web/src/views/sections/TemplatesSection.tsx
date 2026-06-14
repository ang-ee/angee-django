import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
} from "@angee/base";
import type { ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import type { TemplateDescriptor, TemplateInputDescriptor } from "../../data/types";
import { OperatorSection } from "../parts/OperatorSection";

/** Templates pane: the addable template catalog + each template's input schema. */
export function TemplatesSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({ templates: true });
  const templates = snapshot?.templates ?? [];

  return (
    <OperatorSection
      title={t("section.operator.templates.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.templates.loading")}
    >
      {templates.length === 0 ? (
        <EmptyState icon="columns" title={t("operator.templates.empty.title")} />
      ) : (
        <div className="flex flex-col gap-3">
          {templates.map((template) => (
            <TemplateCard key={template.ref} template={template} />
          ))}
        </div>
      )}
    </OperatorSection>
  );
}

function TemplateCard({ template }: { template: TemplateDescriptor }): ReactNode {
  const t = useOperatorT();
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
          <Badge density="compact" shape="pill" tone="neutral">
            {template.kind}
          </Badge>
          <span className="font-mono">{template.path}</span>
        </div>

        {template.inputs.length > 0 ? (
          <div className="flex flex-col gap-1">
            <span className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
              {t("operator.templates.inputs")}
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
  const t = useOperatorT();
  return (
    <div className="flex flex-wrap items-center gap-2 text-13">
      <span className="font-mono text-fg-2">{input.name}</span>
      <Badge density="compact" shape="pill" tone="neutral">
        {input.type ?? "—"}
      </Badge>
      <Badge density="compact" shape="pill" tone={input.required ? "warning" : "neutral"}>
        {input.required ? t("operator.templates.input.required") : t("operator.templates.input.optional")}
      </Badge>
      {input.immutable ? (
        <Badge density="compact" shape="pill" tone="neutral">
          {t("operator.templates.input.immutable")}
        </Badge>
      ) : null}
      {input.generated ? (
        <Badge density="compact" shape="pill" tone="neutral">
          {t("operator.templates.input.generated")}
        </Badge>
      ) : null}
      <span className="text-fg-muted">= {input.default ?? "—"}</span>
    </div>
  );
}
