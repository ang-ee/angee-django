import { useEffect, useRef } from "react";
import { useAuthoredQuery, type TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

import { useUiT } from "../i18n";
import { useRuntimeViewAs } from "../runtime";
import { Banner } from "../ui/alert";
import { Button } from "../ui/button";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { RelationField } from "../widgets/RelationField";

/** Compose into `shell#notices` after the app supplies preview identity.
 * @kind fragment
 */
export function ViewAsBanner() {
  const preview = useRuntimeViewAs();
  const t = useUiT();
  const exitRef = useRef<HTMLButtonElement>(null);
  const userId = preview.viewAs?.userId;
  useEffect(() => {
    if (userId && !preview.pending) exitRef.current?.focus();
  }, [userId, preview.pending]);
  if (!preview.viewAs) return null;
  return <>
    <Banner tone="warning"
      title={t("viewAs.title", { name: preview.currentUser?.name ?? preview.viewAs.userId })}
      actions={<Button ref={exitRef} type="button" size="sm" variant="secondary" disabled={preview.pending} onClick={preview.exit}>
        {t("viewAs.exit")}
      </Button>}
    >
      {t("viewAs.readOnly")}
      {preview.realUser ? ` ${t("viewAs.realUser", { name: preview.realUser.name })}` : null}
    </Banner>
    <ErrorBanner description={preview.error} />
  </>;
}

const ViewablePeople: TypedDocumentNode<{
  viewable_people: { id: string; name: string }[];
}, Record<string, never>> = gql`
  query AngeeViewablePeople { viewable_people { id name: display_name } }
`;

/** Optional record scope intersects IAM's authorized preview candidates.
 * The shell menu mounts this picker on open; its read never gates identity.
 * @kind contribution
 */
export function ViewAsPicker({ userIds, label }: { userIds?: readonly string[]; label?: string } = {}) {
  const preview = useRuntimeViewAs();
  const t = useUiT();
  const pickLabel = label ?? t("viewAs.pick");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const userId = preview.viewAs?.userId;
  const previousUserId = useRef(userId);
  const candidates = useAuthoredQuery(ViewablePeople, {}, {
    dataProviderName: "console",
    enabled: Boolean(preview.currentUser) && !preview.pending,
    models: ["iam.User"],
    errorNotification: false,
  });
  const available = preview.pending ? [] : candidates.data?.viewable_people ?? [];
  const people = userIds === undefined ? available
    : available.filter((person) => userIds.includes(person.id));
  useEffect(() => {
    if (preview.pending) return;
    if (previousUserId.current && !userId) triggerRef.current?.focus();
    previousUserId.current = userId;
  }, [userId, preview.pending]);
  if (!preview.currentUser && !preview.pending) return null;
  if (people.length === 0 && candidates.isSuccess && !preview.pending && !preview.error) return null;
  return <>
    <RelationField
      triggerRef={triggerRef}
      aria-label={pickLabel}
      placeholder={pickLabel}
      value={preview.viewAs?.userId}
      options={people.map((person) => ({ value: person.id, label: person.name }))}
      searchState={{
        pending: candidates.isPending,
        error: candidates.error?.message,
        retry: () => { void candidates.refetch(); },
      }}
      readOnly={preview.pending}
      onChange={preview.enter}
    />
    <ErrorBanner description={candidates.error?.message} />
    {!preview.viewAs ? <ErrorBanner description={preview.error} /> : null}
  </>;
}
