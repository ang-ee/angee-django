import { Button, Glyph, MutationDialog, StatusBadge, actionOutcomeSubmitResult, canonicalOptionValue, formatDateTime, mutationDialogValueCodecs, textRoleVariants, useActionOutcomeMutation, useEnumOptions, useRecordChromeContext, type DescriptorField, type MutationDialogValues, type WidgetOption } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";
import * as React from "react";

import { usePortfolioT } from "./i18n";
import { UPDATE_MODEL } from "./resources";

type UpdateActionName = "report_project_update" | "report_initiative_update";

export interface PortfolioUpdateValues extends Record<string, unknown> {
  health: string;
  body: string;
}

/** Convert dialog values into the action's required-health wire contract. */
export function parsePortfolioUpdateValues(
  values: MutationDialogValues,
  healthOptions: readonly WidgetOption[],
  message = "A portfolio update health assertion is required.",
): PortfolioUpdateValues {
  const health = mutationDialogValueCodecs.requiredString(values.health, "health");
  const canonical = canonicalOptionValue(healthOptions, health);
  if (canonical === undefined) {
    throw new TypeError(message);
  }
  return {
    health: canonical,
    body: mutationDialogValueCodecs.string(values.body) ?? "",
  };
}

/** Current denormalized health as the one status pill, plus its last-report time. */
export function PortfolioHealthSummary({
  health,
  updatedAt,
}: {
  health: unknown;
  updatedAt: unknown;
}): React.ReactElement {
  const t = usePortfolioT();
  const healthOptions = useEnumOptions(UPDATE_MODEL, "health", { casing: "upper" });
  const value = typeof health === "string" ? health : null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusBadge value={value} options={healthOptions}>{value ? undefined : t("update.health.none")}</StatusBadge>
      {typeof updatedAt === "string" || typeof updatedAt === "number" ? (
        <span className={textRoleVariants({ role: "caption" })}>
          {t("update.health.updated")} {formatDateTime(updatedAt)}
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
        <p className={textRoleVariants({ role: "meta" })}>{t("update.pane.description")}</p>
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
      return actionOutcomeSubmitResult(outcome);
    },
    [action, reportInitiative, reportProject, targetId],
  );
  const fields = React.useMemo<readonly DescriptorField[]>(
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
    <MutationDialog<PortfolioUpdateValues>
      trigger={<Button type="button" variant="primary" size="sm">
        <Glyph decorative name="portfolio-update" />
        {t("update.button")}
      </Button>}
      title={t("update.title")}
      description={t("update.description")}
      fields={fields}
      initialValues={{ health: "ON_TRACK" }}
      submitLabel={t("update.submit")}
      submittingLabel={t("update.submitting")}
      errorFallback={t("update.error")}
      parseValues={(values) => parsePortfolioUpdateValues(values, healthOptions, t("update.healthRequired"))}
      onSubmit={submit}
    />
  );
}
