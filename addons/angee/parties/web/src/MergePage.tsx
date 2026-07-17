import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Avatar,
  Button,
  Card,
  Chip,
  Collapsible,
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  Page,
  PageBody,
  PageFooter,
  PageHeader,
  RadioGroup,
  RailPanel,
  Tag,
  TextLink,
  avatarInitials,
  recordPath,
} from "@angee/ui";
import { Link, useNavigate, useParams } from "@tanstack/react-router";

import {
  MergeParties,
  PARTY_MERGE_INVALIDATES,
  PartyMergeComparison,
  VetoMerge,
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
  left: unknown;
  right: unknown;
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

function sameScalar(left: unknown, right: unknown): boolean {
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
  person: MergePersonRecord | null | undefined,
  t: ReturnType<typeof usePartiesT>,
): string {
  const platforms = [...new Set(record.handles.map((handle) => handle.platform))].join(", ");
  if (person?.folder?.name) return t("merge.origin.folder", { name: person.folder.name });
  if (platforms) return t("merge.origin.handles", { platforms });
  return t("merge.origin.manual");
}

function ProvenanceTag({
  provenance,
  label,
}: {
  provenance: Provenance;
  label: string;
}): React.ReactElement {
  const tone = provenance === "both" ? "success" : provenance === "left" ? "info" : "warning";
  return <Tag tone={tone}>{label}</Tag>;
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
          <Avatar size="lg" initials={avatarInitials(record.display_name)} />
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-fg">{record.display_name}</span>
            <span className="block truncate text-xs text-fg-muted">{origin}</span>
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
  const params = useParams({ strict: false }) as { left?: string; right?: string };
  const leftId = params.left ?? "";
  const rightId = params.right ?? "";
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
  const [veto, vetoState] = useAuthoredMutation(VetoMerge, {
    invalidateModels: PARTY_MERGE_INVALIDATES,
  });
  const [survivor, setSurvivor] = React.useState<MergeSide>("left");
  const [fieldChoices, setFieldChoices] = React.useState<Partial<Record<MergeField, MergeSide>>>({});

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
  const busy = mergeState.fetching || vetoState.fetching;

  const goBack = React.useCallback(() => {
    window.history.back();
  }, []);

  const submitMerge = React.useCallback(async () => {
    if (!left || !right) return;
    const into = survivor === "left" ? left : right;
    const source = survivor === "left" ? right : left;
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
      to: recordPath(person ? "/parties/people" : "/parties/organizations", merged.id),
      replace: true,
    });
  }, [differing, fieldChoices, left, leftPerson, merge, navigate, right, rightPerson, survivor]);

  const keepSeparate = React.useCallback(async () => {
    if (!left || !right) return;
    const result = await veto({ aId: left.id, bId: right.id });
    if (result?.veto_merge) goBack();
  }, [goBack, left, right, veto]);

  const crumbs = (
    <>
      <TextLink asChild variant="muted">
        <Link to="/parties/people">{t("merge.breadcrumb.people")}</Link>
      </TextLink>
      <span aria-hidden>/</span>
      <TextLink asChild variant="muted">
        <Link to="/parties/review">{t("merge.breadcrumb.review")}</Link>
      </TextLink>
      <span aria-hidden>/</span>
      <span>{t("merge.title")}</span>
    </>
  );

  if (comparison.fetching && !left && !right) {
    return (
      <Page>
        <PageHeader crumbs={crumbs} title={t("merge.title")} description={t("merge.description")} />
        <PageBody>
          <LoadingPanel message={t("merge.loading")} />
        </PageBody>
      </Page>
    );
  }

  if (comparison.error) {
    return (
      <Page>
        <PageHeader crumbs={crumbs} title={t("merge.title")} description={t("merge.description")} />
        <PageBody>
          <ErrorBanner description={t("merge.error")} />
        </PageBody>
      </Page>
    );
  }

  if (!left || !right || left.id === right.id) {
    return (
      <Page>
        <PageHeader crumbs={crumbs} title={t("merge.title")} description={t("merge.description")} />
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

  return (
    <Page>
      <PageHeader crumbs={crumbs} title={t("merge.title")} description={t("merge.description")} />
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
              origin={originLabel(left, leftPerson, t)}
              survivorLabel={t("merge.survivor")}
            />
            <SurvivorOption
              side="right"
              record={right}
              origin={originLabel(right, rightPerson, t)}
              survivorLabel={t("merge.survivor")}
            />
          </RadioGroup>

          <Card className="p-4 shadow-none">
            <div className="mb-4">
              <h2 className="text-sm font-semibold text-fg">{t("merge.fields.title")}</h2>
              <p className="mt-1 text-13 text-fg-muted">{t("merge.fields.description")}</p>
            </div>
            <div className="grid gap-3">
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
            </div>
            {identical.length > 0 ? (
              <Collapsible variant="section" className={differing.length > 0 ? "mt-3" : undefined}>
                <Collapsible.Trigger>
                  <Collapsible.Icon />
                  {t("merge.fields.identical")}
                  <Tag>{identical.length}</Tag>
                </Collapsible.Trigger>
                <Collapsible.Panel>
                  <div className="grid gap-2">
                    {identical.map((field) => (
                      <div key={field.name} className="flex items-center justify-between gap-3 rounded-6 bg-inset px-3 py-2">
                        <span className="text-13 font-medium text-fg">{t(`merge.field.${field.name}`)}</span>
                        <span className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-13 text-fg-muted">
                            {formatScalar(field.left, t("merge.fields.empty"))}
                          </span>
                          <Tag tone="success">{t("merge.fields.identicalTag")}</Tag>
                        </span>
                      </div>
                    ))}
                  </div>
                </Collapsible.Panel>
              </Collapsible>
            ) : null}
          </Card>

          <div className="grid gap-3 lg:grid-cols-3">
            <RailPanel title={t("merge.preview.handles")} count={handles.length} empty={t("merge.preview.none")}>
              {handles.length > 0 ? (
                <div className="grid gap-2">
                  {handles.map((item) => (
                    <div key={item.key} className="flex min-w-0 items-center justify-between gap-2">
                      <Chip className="min-w-0" mono>{item.value.value}</Chip>
                      <ProvenanceTag
                        provenance={item.provenance}
                        label={t(`merge.provenance.${item.provenance}`)}
                      />
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
                      <ProvenanceTag
                        provenance={item.provenance}
                        label={t(`merge.provenance.${item.provenance}`)}
                      />
                    </div>
                  ))}
                </div>
              ) : null}
            </RailPanel>
            <RailPanel title={t("merge.preview.relationships")} count={relationshipTotal}>
              <div className="grid gap-2 text-13 text-fg-muted">
                <span>{t("merge.relationships.left", { count: leftRelationships })}</span>
                <span>{t("merge.relationships.right", { count: rightRelationships })}</span>
              </div>
            </RailPanel>
          </div>

          <Card className="flex flex-wrap items-center gap-2 p-3 shadow-none">
            <span className="mr-1 text-13 font-semibold text-fg">{t("merge.summary")}</span>
            <Chip>{t("merge.summary.handles", { count: handles.length })}</Chip>
            <Chip>{t("merge.summary.circles", { count: circles.length })}</Chip>
            <Chip>{t("merge.summary.relationships", { count: relationshipTotal })}</Chip>
          </Card>

          <ErrorBanner
            description={
              mergeState.error
                ? t("merge.mutationError")
                : vetoState.error
                  ? t("merge.vetoError")
                  : null
            }
          />
        </div>
      </PageBody>
      <PageFooter className="justify-between">
        <Button
          variant="danger"
          disabled={busy}
          loading={vetoState.fetching}
          loadingText={t("merge.vetoing")}
          onClick={() => void keepSeparate()}
        >
          {t("merge.veto")}
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
