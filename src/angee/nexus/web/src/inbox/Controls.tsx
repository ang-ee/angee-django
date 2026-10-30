import { useAuthoredQuery } from "@angee/refine";
import {
  Filter,
  RelationFieldWidget,

  useResourceView,
  useRelationSelectedOption,
  useEnumOptions,
  optionLabel,
  type FilterClauseField,
  type ResourceToolbarFilterOption,
  type ResourceViewFilter,
} from "@angee/ui";
import { InboxAccounts } from "./documents";
import { INBOX_SOURCE_MODELS } from "./state";
import {
  ALL_ACTIVITY_PRESET,
  COVERAGE_KINDS,
  coverageKindFilter,
  statusCoverage,
  DIRECT_MAIL_PRESET,
  InboxFilter,
  resultLenses,
  type NavigatorLens,
  type ResultLens,
} from "./contract";
import { connectionLabel, useNexusT } from "../i18n";

/**
 * The shared conversation-kind coverage as one choice. Each choice replaces the
 * `kind` field as a unit — Direct narrows to one-to-one threads, "Direct & mail"
 * keeps those plus email while still excluding group chats — so a pick never
 * unions with a stale selection. It edits the same coverage fact the results
 * toolbar owns, so both panes narrow together.
 */
export function useCoverageKind(
  coverage: ResourceViewFilter,
  onCoverage: (next: ResourceViewFilter) => void,
): { value: string; label: string; options: readonly { value: string; label: string }[]; setValue: (next: string) => void } {
  const t = useNexusT();
  let kinds: readonly string[] = [];
  try {
    kinds = new InboxFilter(coverage).values("kind");
  } catch {
    kinds = [];
  }
  const only = kinds.length === 1 ? kinds[0] : undefined;
  const value =
    kinds.length === 0
      ? "any"
      : Filter.from(coverage).hasPreset(ALL_ACTIVITY_PRESET)
        ? "all"
        : Filter.from(coverage).hasPreset(DIRECT_MAIL_PRESET)
        ? "direct-mail"
        : only && (COVERAGE_KINDS as readonly string[]).includes(only)
          ? only
          : "custom";
  const options = [
    { value: "any", label: t("inbox.anyKind") },
    { value: "all", label: t("inbox.allActivity") },
    ...COVERAGE_KINDS.flatMap((kind) =>
      kind === "direct"
        ? [
            { value: "direct", label: t("inbox.direct") },
            { value: "direct-mail", label: t("inbox.directMail") },
          ]
        : [{ value: kind, label: t(`inbox.${kind}`) }],
    ),
    // A non-representable multi-kind coverage (only the results toolbar can
    // build one) surfaces honestly instead of masquerading as "All".
    ...(value === "custom" ? [{ value: "custom", label: t("inbox.customKinds") }] : []),
  ];
  const setValue = (next: string): void => {
    if (next === "custom") return;
    onCoverage(coverageKindFilter(coverage, next));
  };
  return { value, label: options.find((option) => option.value === value)?.label ?? value, options, setValue };
}

export function useResultControls(lens: ResultLens) {
  const t = useNexusT();
  const view = useResourceView();
  const handleRelation = {
    resource: "parties.Handle",
    labelField: "value",
    canCreate: false,
  };
  const handle = useRelationSelectedOption(
    handleRelation,
    new InboxFilter(view.state.filter).one("handle"),
  );
  const roles = useEnumOptions("messaging.Part", "role");
  const platforms = useEnumOptions("messaging.Message", "platform", { casing: "upper" });
  const sources = useAuthoredQuery(InboxAccounts, {}, { models: INBOX_SOURCE_MODELS });
  const coverage = t("inbox.coverage");
  const messages = t("inbox.messages");
  const choices = (values: readonly string[]) =>
    values.map((value) => ({ value, label: t(`inbox.${value}`) }));
  const filterOptions: ResourceToolbarFilterOption[] = [
    ...(sources.data?.inbox_platforms ?? []).map((value) => ({
      id: `platform:${value}`,
      group: coverage,
      label: optionLabel(platforms, value),
      chipLabel: `${t("inbox.platform")}: ${optionLabel(platforms, value)}`,
      filter: { platform: { exact: value } },
    })),
    ...(sources.data?.inbox_accounts ?? []).map((row) => ({
      id: `account:${row.id}`,
      group: coverage,
      label: row.display_name,
      chipLabel: `${t("inbox.account")}: ${row.display_name}`,
      filter: { account: { exact: row.id } },
    })),
    ...COVERAGE_KINDS.map((value) => ({
      id: `kind:${value}`,
      group: coverage,
      label: t(`inbox.${value}`),
      filter: { kind: { exact: value } },
    })),
    {
      id: "direct-mail",
      group: coverage,
      label: t("inbox.directMail"),
      filter: DIRECT_MAIL_PRESET,
    },
    ...["quoted", "starred"].map((field) => ({
      id: field,
      group: messages,
      label: t(`inbox.${field}`),
      filter: { [field]: { exact: "yes" } },
    })),
    ...(sources.data?.inbox_relation_kinds ?? []).map((value) => ({
      id: `relations:${value}`,
      group: messages,
      label: connectionLabel(t, "relation", value) ?? value,
      filter: { relations: { exact: value } },
    })),
    ...(lens === "text"
      ? roles.filter(option => ["title", "body", "quoted", "signature"].includes(option.value)).map(option => ({
          id: `role:${option.value}`,
          group: messages,
          label: `${t("inbox.textRole")}: ${option.label}`,
          filter: { role: { exact: option.value } },
        }))
      : []),
  ];
  const customFilterFields: FilterClauseField[] = [
    {
      id: "period",
      label: t("inbox.period"),
      group: coverage,
      type: "selection",
      operators: ["exact"],
      options: choices(["today", "7days", "30days"]),
    },
    {
      id: "start",
      label: t("inbox.start"),
      group: coverage,
      type: "date",
      operators: ["exact"],
    },
    {
      id: "end",
      label: t("inbox.end"),
      group: coverage,
      type: "date",
      operators: ["exact"],
    },
    {
      id: "timezone",
      label: t("inbox.timezone"),
      group: coverage,
      type: "text",
      operators: ["exact"],
    },
    {
      id: "attachment",
      label: t("inbox.attachment"),
      group: messages,
      type: "selection",
      operators: ["exact"],
      options: choices(["any", "image", "video", "audio", "document", "none"]),
    },
    {
      id: "direction",
      label: t("inbox.direction"),
      group: messages,
      type: "selection",
      operators: ["exact"],
      options: choices(["inbound", "outbound"]),
    },
    {
      id: "handle",
      label: t("inbox.exactHandle"),
      group: messages,
      operators: ["exact"],
      options: handle ? [handle] : [],
      renderValue: ({ value, onValueChange }) => (
        <RelationFieldWidget
          relation={handleRelation}
          searchFields={["value", "display_name"]}
          value={value}
          onChange={onValueChange}
          aria-label={t("inbox.exactHandle")}
        />
      ),
    },
  ];
  return {
    filterOptions,
    customFilterFields,
    groupOptions: resultLenses[lens].axes.map((axis) => ({
      id: axis,
      label: axis === "conversation" && statusCoverage(view.state.filter).includes ? t("inbox.axis.source") : t(`inbox.axis.${axis}`),
      group: { field: `by_${axis}` },
    })),
  };
}

export function useNavigatorControls(lens: NavigatorLens) {
  const t = useNexusT();
  const identity =
    lens === "senders" || lens === "recipients" || lens === "handles";
  const filterOptions: ResourceToolbarFilterOption[] = [
    ...(lens === "senders" || lens === "handles"
      ? [
          {
            id: "sent",
            label: t("inbox.sent"),
            filter: { sent: { exact: "yes" } },
          },
        ]
      : []),
    ...(identity
      ? [
          {
            id: "fading",
            label: t("inbox.fading"),
            filter: { fading: { exact: "yes" } },
          },
        ]
      : []),
  ];
  const customFilterFields: FilterClauseField[] = identity
    ? [
        {
          id: "link",
          label: t("inbox.linkState"),
          type: "selection",
          operators: ["exact"],
          options: ["confirmed", "suggested", "unlinked"].map((value) => ({
            value,
            label: t(`inbox.${value}`),
          })),
        },
      ]
    : [];
  return { filterOptions, customFilterFields };
}
