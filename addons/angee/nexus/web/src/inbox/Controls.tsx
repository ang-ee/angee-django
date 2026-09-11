import { useAuthoredQuery } from "@angee/refine";
import {
  Button,
  Filter,
  RelationFieldWidget,
  Select,
  useResourceView,
  useRelationSelectedOption,
  useEnumOptions,
  type ResourceToolbarFilterField,
  type ResourceToolbarFilterOption,
} from "@angee/ui";
import { InboxAccounts } from "./documents";
import { INBOX_MODELS } from "./state";
import {
  COVERAGE_FIELDS,
  InboxFilter,
  resultLenses,
  type NavigatorLens,
  type ResultLens,
} from "./contract";
import { useNexusT } from "../i18n";

/** Sort writes the same native collection state as the sortable column headers. */
export function InboxOrder({ navigator = false }: { navigator?: boolean }) {
  const t = useNexusT();
  const view = useResourceView();
  const options = navigator
    ? [
        { value: "latest", label: t("inbox.recent") },
        { value: "label", label: t("inbox.name") },
        { value: "count", label: t("inbox.count") },
        { value: "first", label: t("inbox.first") },
      ]
    : [
        { value: "latest", label: t("inbox.latest") },
        { value: "oldest", label: t("inbox.oldest") },
      ];
  const sort = view.state.sorting?.[0];
  return (
    <Select
      size="sm"
      aria-label={t("inbox.order")}
      className="w-36"
      options={options}
      value={
        !navigator && sort?.desc === false ? "oldest" : (sort?.id ?? "latest")
      }
      onValueChange={(value) =>
        view.setSorting([
          {
            id: value === "oldest" ? "latest" : value,
            desc: value === "latest" || value === "count",
          },
        ])
      }
    />
  );
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
  const sources = useAuthoredQuery(InboxAccounts, {}, { models: INBOX_MODELS });
  const coverage = t("inbox.coverage");
  const messages = t("inbox.messages");
  const choices = (values: readonly string[]) =>
    values.map((value) => ({ value, label: t(`inbox.${value}`) }));
  const filterOptions: ResourceToolbarFilterOption[] = [
    ...(sources.data?.inbox_platforms ?? []).map((value) => ({
      id: `platform:${value}`,
      group: coverage,
      label: value,
      chipLabel: `${t("inbox.platform")}: ${value}`,
      filter: { platform: { exact: value } },
    })),
    ...(sources.data?.inbox_accounts ?? []).map((row) => ({
      id: `account:${row.id}`,
      group: coverage,
      label: row.display_name,
      chipLabel: `${t("inbox.account")}: ${row.display_name}`,
      filter: { account: { exact: row.id } },
    })),
    ...["mail", "direct", "group", "other"].map((value) => ({
      id: `kind:${value}`,
      group: coverage,
      label: t(`inbox.${value}`),
      filter: { kind: { exact: value } },
    })),
    {
      id: "direct-mail",
      preset: true,
      group: coverage,
      label: t("inbox.directMail"),
      filter: { kind: { inList: ["direct", "mail"] } },
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
      label: t(`inbox.relation.${value}`),
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
  const customFilterFields: ResourceToolbarFilterField[] = [
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
      label: t(`inbox.axis.${axis}`),
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
  const customFilterFields: ResourceToolbarFilterField[] = identity
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

/** Clear controls operate on the native filter object and preserve the other collection facts. */
export function InboxClearFilters({ finder = false }: { finder?: boolean }) {
  const t = useNexusT();
  const view = useResourceView();
  const filter = Filter.from(view.state.filter);
  if (!filter.hasEntries()) return null;
  if (finder)
    return (
      <Button size="sm" variant="ghost" onClick={() => view.setFilter({})}>
        {t("inbox.clearFinder")}
      </Button>
    );
  return (
    <>
      {Filter.from(filter.onlyFields(COVERAGE_FIELDS)).hasEntries() ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => view.setFilter(filter.withoutFields(COVERAGE_FIELDS))}
        >
          {t("inbox.clearCoverage")}
        </Button>
      ) : null}
      {Filter.from(filter.withoutFields(COVERAGE_FIELDS)).hasEntries() ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => view.setFilter(filter.onlyFields(COVERAGE_FIELDS))}
        >
          {t("inbox.clearMessages")}
        </Button>
      ) : null}
    </>
  );
}
