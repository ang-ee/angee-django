import { useEffect, useRef } from "react";

import { useUiT } from "../i18n";
import { useRuntimeViewAs } from "../runtime";
import { Banner } from "../ui/alert";
import { Button } from "../ui/button";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { RelationField } from "../widgets/RelationField";

/** Compose into CONSOLE_NOTICE_SLOT after the app supplies preview identity. */
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

/** Optional record scope intersects the identity owner's authorized people. */
export function ViewAsPicker({ userIds, label }: { userIds?: readonly string[]; label?: string } = {}) {
  const preview = useRuntimeViewAs();
  const t = useUiT();
  const pickLabel = label ?? t("viewAs.pick");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const userId = preview.viewAs?.userId;
  const previousUserId = useRef(userId);
  const people = userIds === undefined ? preview.viewablePeople
    : preview.viewablePeople.filter((person) => userIds.includes(person.id));
  useEffect(() => {
    if (preview.pending) return;
    if (previousUserId.current && !userId) triggerRef.current?.focus();
    previousUserId.current = userId;
  }, [userId, preview.pending]);
  if (people.length === 0 && (userIds !== undefined || (!preview.pending && !preview.error))) return null;
  return <>
    <RelationField
      triggerRef={triggerRef}
      aria-label={pickLabel}
      placeholder={pickLabel}
      value={preview.viewAs?.userId}
      options={people.map((person) => ({ value: person.id, label: person.name }))}
      readOnly={preview.pending}
      onChange={preview.enter}
    />
    {!preview.viewAs ? <ErrorBanner description={preview.error} /> : null}
  </>;
}
