import { Filter, ResourceQuery, type FilterOperator } from "@angee/metadata";
import type {
  CollectionPageRequest,
  ResourceViewFilter,
  ResourceViewGroup,
} from "@angee/ui";

export const NAVIGATOR_LENSES = [
  "senders",
  "recipients",
  "handles",
  "groups",
  "circles",
] as const;
export type NavigatorLens = (typeof NAVIGATOR_LENSES)[number];
export const RESULT_LENSES = [
  "conversations",
  "timeline",
  "attachments",
  "text",
] as const;
export type ResultLens = (typeof RESULT_LENSES)[number];
export const NAVIGATOR_AXES = [
  "recency",
  "circle",
  "group",
  "platform",
  "account",
  "link",
  "organization",
] as const;
export const RESULT_AXES = [
  "conversation",
  "day",
  "account",
  "platform",
  "sender",
] as const;
export const COVERAGE_FIELDS = [
  "platform",
  "account",
  "kind",
  "period",
  "start",
  "end",
  "after",
  "before",
  "timezone",
] as const;

/** The row unit and compatible axes are one declaration, shared by controls and transport. */
export const resultLenses: Record<
  ResultLens,
  { group: string | null; axes: readonly string[] }
> = {
  conversations: {
    group: "conversation",
    axes: ["conversation", "day", "account", "platform"],
  },
  timeline: {
    group: "day",
    axes: ["conversation", "day", "account", "platform"],
  },
  attachments: {
    group: null,
    axes: ["conversation", "account", "platform", "sender"],
  },
  text: {
    group: null,
    axes: ["conversation", "account", "platform", "sender"],
  },
};

export function navigatorAxes(lens: NavigatorLens): readonly string[] {
  return lens === "circles"
    ? []
    : lens === "groups"
      ? ["recency", "platform", "account"]
      : NAVIGATOR_AXES;
}

export function resultLensForGroup(
  lens: ResultLens,
  group: ResourceViewGroup | undefined,
): ResultLens {
  if (lens === "attachments" || lens === "text") return lens;
  return group?.field === "by_conversation" ? "conversations" : "timeline";
}

const fields = {
  id: ["exact"],
  label: [],
  latest: [],
  count: [],
  first: [],
  text: ["iContains"],
  platform: ["exact", "inList"],
  account: ["exact", "inList"],
  kind: ["exact", "inList"],
  period: ["exact"],
  start: ["exact"],
  end: ["exact"],
  after: ["exact"],
  before: ["exact"],
  timezone: ["exact"],
  quoted: ["exact"],
  attachment: ["exact"],
  direction: ["exact"],
  starred: ["exact"],
  handle: ["exact"],
  relations: ["exact", "inList"],
  role: ["exact", "inList"],
  link: ["exact"],
  sent: ["exact"],
  fading: ["exact"],
} satisfies Record<string, readonly FilterOperator[]>;

/** Authored projection language; semantic bucket fields cannot collide with coverage fields. */
export function inboxCollectionQuery(axes: readonly string[]): ResourceQuery {
  const declarations = Object.fromEntries([
    ...Object.keys(fields).map((field) => [field, { scalar: "String" }]),
    ...axes.map((axis) => [`by_${axis}`, { scalar: "String" }]),
  ]);
  const contract = ResourceQuery.forRows({ fields: declarations }).contract;
  for (const [field, definition] of Object.entries(contract.fields)) {
    const operators: readonly FilterOperator[] = field.startsWith("by_")
      ? ["exact", "isNull"]
      : fields[field as keyof typeof fields];
    definition.filter = operators.length
      ? { field, scalar: "String", values: [], operators }
      : null;
    definition.sort = ["label", "latest", "count", "first"].includes(field)
      ? { field }
      : null;
  }
  contract.axes = Object.fromEntries(
    axes.map((axis) => {
      const field = `by_${axis}`;
      return [
        field,
        {
          field,
          kind: "column" as const,
          identityPath: field,
          paths: [field],
          extractions: [],
          server: {
            input: field,
            key: field,
            labelInput: "label",
            labelKey: "label",
          },
          drill: {
            kind: "value" as const,
            field,
            valueKey: field,
            nullMode: "isNull" as const,
            valueMap: [],
          },
        },
      ];
    }),
  );
  return ResourceQuery.fromContract(contract);
}

/** Typed-input translation rejects unsupported predicates instead of widening a query. */
export class InboxFilter {
  private readonly comparisons;

  constructor(filter: ResourceViewFilter | undefined) {
    this.comparisons = Filter.from(filter).conjunctions();
  }

  values(field: string): string[] {
    const predicates = this.comparisons.filter((item) => item.field === field);
    let values: string[] | undefined;
    for (const predicate of predicates) {
      if (!["exact", "inList", "iContains"].includes(predicate.operator))
        throw new Error(`Unsupported ${field} comparison.`);
      const items = Array.isArray(predicate.value)
        ? predicate.value
        : [predicate.value];
      if (!items.every((value): value is string => typeof value === "string"))
        throw new Error(`Invalid ${field} value.`);
      values =
        values === undefined
          ? [...items]
          : values.filter((value) => items.includes(value));
    }
    if (values?.length === 0 && predicates.length > 0)
      throw new Error(`No results: conflicting ${field} filters.`);
    return values ?? [];
  }

  one(field: string): string {
    const values = this.values(field);
    if (values.length > 1) throw new Error(`Choose one ${field} value.`);
    return values[0] ?? "";
  }

  coverage(timezone: string) {
    return {
      platforms: this.values("platform"),
      accounts: this.values("account"),
      kinds: this.values("kind"),
      start: this.one("start") || null,
      end: this.one("end") || null,
      after: this.one("after") || null,
      before: this.one("before") || null,
      period: this.one("period"),
      timezone: this.one("timezone") || timezone,
    };
  }

  search() {
    return {
      text: this.one("text"),
      quoted: this.one("quoted") === "yes",
      attachment: this.one("attachment"),
      direction: this.one("direction"),
      starred: this.one("starred") === "yes",
      handle: this.one("handle"),
      relations: this.values("relations"),
    };
  }

  scope() {
    const fields = [
      ...new Set(
        this.comparisons
          .filter((item) => item.field.startsWith("by_"))
          .map((item) => item.field),
      ),
    ];
    if (fields.length > 1) throw new Error("Choose one grouping axis.");
    const field = fields[0];
    if (!field) return null;
    const isNull = this.comparisons.some(
      (item) =>
        item.field === field &&
        item.operator === "isNull" &&
        item.value === true,
    );
    if (
      isNull &&
      this.comparisons.some(
        (item) => item.field === field && item.operator !== "isNull",
      )
    )
      throw new Error("No results: conflicting group filters.");
    return { axis: field.slice(3), value: isNull ? null : this.one(field) };
  }
}

export function navigatorOrder(request: CollectionPageRequest): string {
  const field = Object.keys(request.order ?? {})[0] ?? "latest";
  return (
    { latest: "recent", label: "name", count: "count", first: "first" } as const
  )[field as "latest" | "label" | "count" | "first"];
}
