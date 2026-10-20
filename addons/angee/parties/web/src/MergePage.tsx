import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Avatar, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Chip, Collapsible, CountBadge, EmptyState, ErrorBanner,
  LoadingPanel, MetaGrid, Page, PageBody, PageFooter, PageHeader, RadioGroup, RailPanel, avatarInitials, optionLabel, useConfirm,
  useEnumOptions, useRouteHref, useRouteParam, type WidgetOption,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import {
  MergeParties,
  PARTY_MERGE_INVALIDATES,
  PartyMergeComparison,
  DismissSameAs,
  type MergePartyRecord,
  type MergePersonRecord,
} from "./documents";
import { usePartiesT } from "./i18n";

type MergeSide = "left" | "right";
type MergeHandle = MergePartyRecord["handles"][number];
type MergeCircleMember = MergePartyRecord["circle_members"][number];
type Provenance = MergeSide | "both";

const COMMON_FIELDS = ["display_name", "notes", "first_met_note"] as const;
const PERSON_FIELDS = [
  "name_prefix",
  "given_name",
  "additional_name",
  "family_name",
  "name_suffix",
  "nickname",
  "birthday",
] as const;

type CommonField = (typeof COMMON_FIELDS)[number];
type PersonField = (typeof PERSON_FIELDS)[number];
type MergeField = CommonField | PersonField;

interface FieldComparison {
  name: MergeField;
  left: string | null;
  right: string | null;
}

interface UnionItem<T> {
  key: string;
  value: T;
  provenance: Provenance;
}

function fieldComparisons(
  left: MergePartyRecord,
  right: MergePartyRecord,
  leftPerson: MergePersonRecord | null | undefined,
  rightPerson: MergePersonRecord | null | undefined,
): readonly FieldComparison[] {
  const common = COMMON_FIELDS.map((name) => ({
    name,
    left: left[name],
    right: right[name],
  }));
  if (!leftPerson || !rightPerson) return common;
  return [
    ...common,
    ...PERSON_FIELDS.map((name) => ({
      name,
      left: leftPerson[name],
      right: rightPerson[name],
    })),
  ];
}

function sameScalar(left: string | null, right: string | null): boolean {
  return (left ?? "") === (right ?? "");
}

function unionBy<T>(
  left: readonly T[],
  right: readonly T[],
  keyFor: (value: T) => string,
): readonly UnionItem<T>[] {
  const items = new Map<string, UnionItem<T>>();
  for (const value of left) {
    const key = keyFor(value);
    items.set(key, { key, value, provenance: "left" });
  }
  for (const value of right) {
    const key = keyFor(value);
    const existing = items.get(key);
    items.set(key, {
      key,
      value: existing?.value ?? value,
      provenance: existing ? "both" : "right",
    });
  }
  return [...items.values()];
}

function relationshipCount(record: MergePartyRecord): number {
  return record.relationships.length + record.inbound_relationships.length;
}

function relationshipIds(record: MergePartyRecord): readonly string[] {
  return [
    ...record.relationships.map((relationship) => relationship.id),
    ...record.inbound_relationships.map((relationship) => relationship.id),
  ];
}

function originLabel(
  record: MergePartyRecord,
  platforms: readonly WidgetOption[],
  t: ReturnType<typeof usePartiesT>,
): string {
  const names = [...new Set(record.handles.map((handle) => optionLabel(platforms, handle.platform)))].join(", ");
  if (names) return t("merge.origin.handles", { platforms: names });
  return t("merge.origin.manual");
}

/** Where a merged item comes from: both records, or only one, named. */
function ProvenanceChip({ provenance, names }: { provenance: Provenance; names: Record<MergeSide, string> }): React.ReactElement {
  const t = usePartiesT();
  return provenance === "both"
    ? <Chip tone="success">{t("merge.provenance.both")}</Chip>
    : <Chip tone="neutral">{t("merge.provenance.only", { name: names[provenance] })}</Chip>;
}

function SurvivorOption({
  side,
  record,
  origin,
  survivorLabel,
}: {
  side: MergeSide;
  record: MergePartyRecord;
  origin: string;
  survivorLabel: string;
}): React.ReactElement {
  return (
    <RadioGroup.Item
      value={side}
      variant="card"
      label={
        <span className="flex min-w-0 items-center gap-3">
          <Avatar
            size="lg"
            src={record.display_avatar?.url || undefined}
            initials={avatarInitials(record.display_name)}
          />
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-fg">{record.display_name}</span>
            <span className="block truncate text-12 text-fg-muted">{origin}</span>
          </span>
        </span>
      }
      description={survivorLabel}
    />
  );
}

/** Compare two parties, select the survivor, and execute the model-owned merge. */
export function MergePage(): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const leftId = useRouteParam("left") ?? "";
  const rightId = useRouteParam("right") ?? "";
  const variables = React.useMemo(
    () => ({ left: leftId, right: rightId }),
    [leftId, rightId],
  );
  const comparison = useAuthoredQuery(PartyMergeComparison, variables, {
    enabled: Boolean(leftId && rightId && leftId !== rightId),
    models: PARTY_MERGE_INVALIDATES,
  });
  const [merge, mergeState] = useAuthoredMutation(MergeParties, {
    invalidateModels: PARTY_MERGE_INVALIDATES,
  });
  const [dismiss, dismissState] = useAuthoredMutation(DismissSameAs, {
    invalidateModels: PARTY_MERGE_INVALIDATES,
  });
  const [survivor, setSurvivor] = React.useState<MergeSide>("left");
  const [fieldChoices, setFieldChoices] = React.useState<Partial<Record<MergeField, MergeSide>>>({});
  const platforms = useEnumOptions("parties.Handle", "platform", { casing: "upper" });
  const confirm = useConfirm();

  React.useEffect(() => {
    setSurvivor("left");
    setFieldChoices({});
  }, [leftId, rightId]);

  const left = comparison.data?.left?.id === leftId ? comparison.data.left : null;
  const right = comparison.data?.right?.id === rightId ? comparison.data.right : null;
  const leftPerson = comparison.data?.left_person?.id === leftId ? comparison.data.left_person : null;
  const rightPerson = comparison.data?.right_person?.id === rightId ? comparison.data.right_person : null;
  const fields = React.useMemo(
    () => (left && right ? fieldComparisons(left, right, leftPerson, rightPerson) : []),
    [left, leftPerson, right, rightPerson],
  );
  const differing = fields.filter((field) => !sameScalar(field.left, field.right));
  const identical = fields.filter((field) => sameScalar(field.left, field.right));
  const handles = React.useMemo(
    () =>
      left && right
        ? unionBy<MergeHandle>(left.handles, right.handles, (handle) =>
            `${handle.platform}:${handle.normalized_value}`,
          )
        : [],
    [left, right],
  );
  const circles = React.useMemo(
    () =>
      left && right
        ? unionBy<MergeCircleMember>(left.circle_members, right.circle_members, (member) =>
            member.circle?.id ?? member.id,
          )
        : [],
    [left, right],
  );
  const leftRelationships = left ? relationshipCount(left) : 0;
  const rightRelationships = right ? relationshipCount(right) : 0;
  const relationshipTotal = left && right
    ? new Set([...relationshipIds(left), ...relationshipIds(right)]).size
    : 0;
  const busy = mergeState.fetching || dismissState.fetching;

  // The merge flow's home is the review queue (its route parent).
  const goBack = React.useCallback(() => {
    void navigate({ to: routeHref("parties.review") });
  }, [navigate, routeHref]);

  const submitMerge = React.useCallback(async () => {
    if (!left || !right) return;
    const into = survivor === "left" ? left : right;
    const source = survivor === "left" ? right : left;
    // Merging is irreversible: the source record disappears into the survivor.
    const ok = await confirm({
      title: t("merge.confirmTitle", { name: into.display_name }),
      body: t("merge.confirmBody", { source: source.display_name, name: into.display_name }),
      confirm: t("merge.submit", { name: into.display_name }),
      danger: true,
    });
    if (!ok) return;
    const fieldOverrides = Object.fromEntries(
      differing
        .filter((field) => (fieldChoices[field.name] ?? survivor) !== survivor)
        .map((field) => [field.name, survivor === "left" ? field.right : field.left]),
    );
    const result = await merge({
      intoId: into.id,
      fromId: source.id,
      fieldOverrides,
    });
    const merged = result?.merge_parties;
    if (!merged) return;
    const person = survivor === "left" ? leftPerson : rightPerson;
    void navigate({
      to: routeHref(
        person ? "parties.people.record" : "parties.organizations.record",
        { id: merged.id },
      ),
      replace: true,
    });
  }, [confirm, differing, fieldChoices, left, leftPerson, merge, navigate, right, rightPerson, routeHref, survivor, t]);

  const keepSeparate = React.useCallback(async () => {
    if (!left || !right) return;
    const result = await dismiss({ aId: left.id, bId: right.id });
    if (result?.dismiss_same_as) goBack();
  }, [goBack, left, right, dismiss]);

  if (comparison.isFetching && !left && !right) {
    return (
      <Page>
        <PageHeader title={t("merge.title")} description={t("merge.description")} />
        <PageBody>
          <LoadingPanel message={t("merge.loading")} />
        </PageBody>
      </Page>
    );
  }

  if (comparison.error) {
    return (
      <Page>
        <PageHeader title={t("merge.title")} description={t("merge.description")} />
        <PageBody>
          <ErrorBanner description={t("merge.error")} />
        </PageBody>
      </Page>
    );
  }

  if (!left || !right || left.id === right.id) {
    return (
      <Page>
        <PageHeader title={t("merge.title")} description={t("merge.description")} />
        <PageBody>
          <EmptyState
            icon="users"
            title={t("merge.notFound.title")}
            description={t("merge.notFound.description")}
          />
        </PageBody>
      </Page>
    );
  }

  const survivorRecord = survivor === "left" ? left : right;
  const names: Record<MergeSide, string> = { left: left.display_name, right: right.display_name };

  return (
    <Page>
      <PageHeader title={t("merge.title")} description={t("merge.description")} />
      <PageBody>
        <div className="mx-auto grid w-full max-w-6xl gap-5">
          <RadioGroup
            aria-label={t("merge.survivor")}
            value={survivor}
            onValueChange={(value) => setSurvivor(value as MergeSide)}
            variant="card"
            className="grid gap-3 md:grid-cols-2"
          >
            <SurvivorOption
              side="left"
              record={left}
              origin={originLabel(left, platforms, t)}
              survivorLabel={t("merge.survivor")}
            />
            <SurvivorOption
              side="right"
              record={right}
              origin={originLabel(right, platforms, t)}
              survivorLabel={t("merge.survivor")}
            />
          </RadioGroup>

          <Card className="shadow-none">
            <CardHeader>
              <CardTitle>{t("merge.fields.title")}</CardTitle>
              <CardDescription>{t("merge.fields.description")}</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3">
              {differing.map((field) => (
                <div key={field.name} className="grid gap-2 border-b border-border-subtle pb-3 last:border-0">
                  <span className="text-13 font-medium text-fg">{t(`merge.field.${field.name}`)}</span>
                  <RadioGroup
                    aria-label={t(`merge.field.${field.name}`)}
                    value={fieldChoices[field.name] ?? survivor}
                    onValueChange={(value) =>
                      setFieldChoices((current) => ({ ...current, [field.name]: value as MergeSide }))
                    }
                    variant="card"
                    className="grid gap-2 md:grid-cols-2"
                  >
                    <RadioGroup.Item
                      value="left"
                      variant="card"
                      label={formatScalar(field.left, t("merge.fields.empty"))}
                    />
                    <RadioGroup.Item
                      value="right"
                      variant="card"
                      label={formatScalar(field.right, t("merge.fields.empty"))}
                    />
                  </RadioGroup>
                </div>
              ))}
            </CardContent>
            {identical.length > 0 ? (
              <CardContent>
                <Collapsible variant="section">
                  <Collapsible.Trigger>
                    <Collapsible.Icon />
                    {t("merge.fields.identical")}
                    <CountBadge value={identical.length} />
                  </Collapsible.Trigger>
                  <Collapsible.Panel>
                    <MetaGrid rows={identical.map((field) => ({
                      id: field.name,
                      label: t(`merge.field.${field.name}`),
                      value: formatScalar(field.left, t("merge.fields.empty")),
                    }))} />
                  </Collapsible.Panel>
                </Collapsible>
              </CardContent>
            ) : null}
          </Card>

          <div className="grid gap-3 lg:grid-cols-3">
            <RailPanel title={t("merge.preview.handles")} count={handles.length} empty={t("merge.preview.none")}>
              {handles.length > 0 ? (
                <div className="grid gap-2">
                  {handles.map((item) => (
                    <div key={item.key} className="flex min-w-0 items-center justify-between gap-2">
                      <Chip className="min-w-0" mono>{item.value.value}</Chip>
                      <ProvenanceChip provenance={item.provenance} names={names} />
                    </div>
                  ))}
                </div>
              ) : null}
            </RailPanel>
            <RailPanel title={t("merge.preview.circles")} count={circles.length} empty={t("merge.preview.none")}>
              {circles.length > 0 ? (
                <div className="grid gap-2">
                  {circles.map((item) => (
                    <div key={item.key} className="flex min-w-0 items-center justify-between gap-2">
                      <Chip>{item.value.circle?.name ?? t("merge.preview.none")}</Chip>
                      <ProvenanceChip provenance={item.provenance} names={names} />
                    </div>
                  ))}
                </div>
              ) : null}
            </RailPanel>
            <RailPanel title={t("merge.preview.relationships")} count={relationshipTotal}>
              <MetaGrid rows={[
                { id: "left", label: left.display_name, value: leftRelationships },
                { id: "right", label: right.display_name, value: rightRelationships },
              ]} />
            </RailPanel>
          </div>

          <Card className="shadow-none">
            <CardHeader><CardTitle>{t("merge.summary")}</CardTitle></CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              <Chip>{t("merge.summary.handles", { count: handles.length })}</Chip>
              <Chip>{t("merge.summary.circles", { count: circles.length })}</Chip>
              <Chip>{t("merge.summary.relationships", { count: relationshipTotal })}</Chip>
            </CardContent>
          </Card>

          <ErrorBanner
            description={
              mergeState.error
                ? t("merge.mutationError")
                : dismissState.error
                  ? t("merge.dismissError")
                  : null
            }
          />
        </div>
      </PageBody>
      <PageFooter className="justify-between">
        <Button
          variant="danger"
          disabled={busy}
          loading={dismissState.fetching}
          loadingText={t("merge.dismissing")}
          onClick={() => void keepSeparate()}
        >
          {t("merge.dismiss")}
        </Button>
        <span className="flex items-center gap-2">
          <Button disabled={busy} onClick={goBack}>{t("merge.cancel")}</Button>
          <Button
            variant="primary"
            disabled={busy}
            loading={mergeState.fetching}
            loadingText={t("merge.submitting")}
            onClick={() => void submitMerge()}
          >
            {t("merge.submit", { name: survivorRecord.display_name })}
          </Button>
        </span>
      </PageFooter>
    </Page>
  );
}

function formatScalar(value: unknown, empty: string): string {
  if (value === null || value === undefined || value === "") return empty;
  return String(value);
}
