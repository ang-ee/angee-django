import {
  Badge,
  Button,
  Glyph,
  MutationDialog,
  RelativeTime,
  canonicalOptionValue,
  mutationDialogValueCodecs,
  statusTone,
  useActionOutcomeMutation,
  useEnumOptions,
  useRecordChromeContext,
  type MutationDialogField,
  type MutationDialogValues,
  type WidgetOption,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";
import * as React from "react";

import { usePortfolioT } from "./i18n";
import { UPDATE_MODEL } from "./resources";

const HEALTH_VALUES = ["ON_TRACK", "AT_RISK", "OFF_TRACK"] as const;
type KnownPortfolioHealth = (typeof HEALTH_VALUES)[number];
type UpdateActionName = "report_project_update" | "report_initiative_update";

export interface PortfolioUpdateValues extends Record<string, unknown> {
  health: string;
  body: string;
}

/** Convert dialog values into the action's required-health wire contract. */
export function parsePortfolioUpdateValues(
  values: MutationDialogValues,
  healthOptions: readonly WidgetOption[],
): PortfolioUpdateValues {
  const health = mutationDialogValueCodecs
    .requiredString(values.health, "health")
    .toUpperCase();
  const canonical = canonicalOptionValue(healthOptions, health);
  if (canonical === undefined) {
    throw new TypeError("A portfolio update health assertion is required.");
  }
  return {
    health: canonical,
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
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={statusTone(normalized)} shape="pill">
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
  const healthOptions = useEnumOptions(UPDATE_MODEL, "health", { casing: "upper" });
  const [reportProject] = useActionOutcomeMutation<ActionFieldName>(
    "report_project_update", {
      invalidateModels: [UPDATE_MODEL, targetModel],
    },
  );
  const [reportInitiative] = useActionOutcomeMutation<ActionFieldName>(
    "report_initiative_update", {
      invalidateModels: [UPDATE_MODEL, targetModel],
    },
  );
  const submit = React.useCallback(
    async (values: PortfolioUpdateValues) => {
      const outcome = action === "report_project_update"
        ? await reportProject(targetId, values)
        : await reportInitiative(targetId, values);
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
        parseValues={(values) => parsePortfolioUpdateValues(values, healthOptions)}
        onSubmit={submit}
      />
    </>
  );
}

function normalizeHealth(value: unknown): KnownPortfolioHealth | null {
  const normalized = String(value ?? "").trim().toUpperCase();
  return HEALTH_VALUES.includes(normalized as KnownPortfolioHealth)
    ? (normalized as KnownPortfolioHealth)
    : null;
}
