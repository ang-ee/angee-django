import {
  Badge,
  Button,
  Glyph,
  MutationDialog,
  RelativeTime,
  mutationDialogValueCodecs,
  useAuthoredResourceMutation,
  useEnumOptions,
  useRecordChromeContext,
  type MutationDialogField,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import {
  ReportInitiativeUpdateDocument,
  ReportProjectUpdateDocument,
  type PortfolioUpdateActionResult,
} from "./documents";
import { usePortfolioT } from "./i18n";
import { UPDATE_MODEL } from "./resources";

const HEALTH_VALUES = ["ON_TRACK", "AT_RISK", "OFF_TRACK"] as const;
type PortfolioHealth = (typeof HEALTH_VALUES)[number];
type UpdateActionName = "report_project_update" | "report_initiative_update";

export interface PortfolioUpdateValues extends Record<string, unknown> {
  health: PortfolioHealth;
  body: string;
}

/** Convert dialog values into the action's required-health wire contract. */
export function parsePortfolioUpdateValues(
  values: MutationDialogValues,
): PortfolioUpdateValues {
  const health = mutationDialogValueCodecs
    .requiredString(values.health, "health")
    .toUpperCase();
  if (!HEALTH_VALUES.includes(health as PortfolioHealth)) {
    throw new TypeError("A portfolio update health assertion is required.");
  }
  return {
    health: health as PortfolioHealth,
    body: mutationDialogValueCodecs.string(values.body) ?? "",
  };
}

/** Current denormalized health plus its last-report timestamp. */
export function PortfolioHealthSummary({
  health,
  updatedAt,
}: {
  health: unknown;
  updatedAt: unknown;
}): React.ReactElement {
  const t = usePortfolioT();
  const normalized = normalizeHealth(health);
  const copy = {
    ON_TRACK: t("update.health.onTrack"),
    AT_RISK: t("update.health.atRisk"),
    OFF_TRACK: t("update.health.offTrack"),
  } as const;
  const tone = {
    ON_TRACK: "success",
    AT_RISK: "warning",
    OFF_TRACK: "danger",
  } as const;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={normalized ? tone[normalized] : "neutral"} shape="pill">
        {normalized ? copy[normalized] : t("update.health.none")}
      </Badge>
      {typeof updatedAt === "string" || typeof updatedAt === "number" ? (
        <span className="text-12 text-fg-muted">
          {t("update.health.updated")} <RelativeTime value={updatedAt} />
        </span>
      ) : null}
    </div>
  );
}

/** Project update composer rendered inside the portfolio-owned FormView tab. */
export function ProjectUpdatesSection(): React.ReactElement {
  return <RecordUpdatesSection action="report_project_update" />;
}

/** Initiative update composer rendered inside the portfolio-owned FormView tab. */
export function InitiativeUpdatesSection(): React.ReactElement {
  return <RecordUpdatesSection action="report_initiative_update" />;
}

function RecordUpdatesSection({
  action,
}: {
  action: UpdateActionName;
}): React.ReactElement {
  const t = usePortfolioT();
  const context = useRecordChromeContext();
  return (
    <div className="grid gap-4">
      <div className="grid gap-1">
        <PortfolioHealthSummary
          health={context.record?.health}
          updatedAt={context.record?.health_updated_at}
        />
        <p className="text-13 text-fg-muted">{t("update.pane.description")}</p>
      </div>
      <PortfolioUpdateComposer
        action={action}
        targetId={context.recordId}
        targetModel={context.resource}
      />
    </div>
  );
}

export interface PortfolioUpdateComposerProps {
  action: UpdateActionName;
  targetId: string;
  targetModel: string;
}

/** Required-health composer shared by project and initiative record tabs. */
export function PortfolioUpdateComposer({
  action,
  targetId,
  targetModel,
}: PortfolioUpdateComposerProps): React.ReactElement {
  const t = usePortfolioT();
  const [open, setOpen] = React.useState(false);
  const declaredOptions = useEnumOptions(UPDATE_MODEL, "health");
  const healthOptions = React.useMemo(
    () =>
      declaredOptions.map((option) => ({
        ...option,
        value: String(option.value).toUpperCase(),
      })),
    [declaredOptions],
  );
  const [reportProject] = useAuthoredResourceMutation(
    ReportProjectUpdateDocument,
    {
      invalidateModels: [UPDATE_MODEL, targetModel],
      shouldInvalidate: (data) => data?.report_project_update.ok === true,
    },
  );
  const [reportInitiative] = useAuthoredResourceMutation(
    ReportInitiativeUpdateDocument,
    {
      invalidateModels: [UPDATE_MODEL, targetModel],
      shouldInvalidate: (data) => data?.report_initiative_update.ok === true,
    },
  );
  const submit = React.useCallback(
    async (values: PortfolioUpdateValues) => {
      let outcome: PortfolioUpdateActionResult | undefined;
      if (action === "report_project_update") {
        const data = await reportProject({ id: targetId, ...values });
        outcome = data?.report_project_update;
      } else {
        const data = await reportInitiative({ id: targetId, ...values });
        outcome = data?.report_initiative_update;
      }
      if (!outcome?.ok) {
        throw new Error(outcome?.message || t("update.error"));
      }
    },
    [action, reportInitiative, reportProject, t, targetId],
  );
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "health",
        label: t("common.health"),
        widget: "select",
        required: true,
        options: healthOptions,
      },
      {
        name: "body",
        label: t("update.body"),
        widget: "textarea",
      },
    ],
    [healthOptions, t],
  );

  return (
    <>
      <Button type="button" variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="portfolio-update" />
        {t("update.button")}
      </Button>
      <MutationDialog<PortfolioUpdateValues>
        open={open}
        onOpenChange={setOpen}
        title={t("update.title")}
        description={t("update.description")}
        fields={fields}
        initialValues={{ health: "ON_TRACK" }}
        submitLabel={t("update.submit")}
        submittingLabel={t("update.submitting")}
        errorFallback={t("update.error")}
        parseValues={parsePortfolioUpdateValues}
        onSubmit={submit}
      />
    </>
  );
}

function normalizeHealth(value: unknown): PortfolioHealth | null {
  const normalized = String(value ?? "").trim().toUpperCase();
  return HEALTH_VALUES.includes(normalized as PortfolioHealth)
    ? (normalized as PortfolioHealth)
    : null;
}
