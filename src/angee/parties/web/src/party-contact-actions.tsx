import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { AuthoredMutateOptions } from "@angee/refine";
import {
  Action,
  REFINE_CREATE_ID,
  useActionOutcomeMutation,
  type ActionArg,
  type ActionDescriptor,
  type ActionFormContext,
} from "@angee/ui";

import { PARTY_STATEMENT_DECISION_INVALIDATES } from "./documents";
import { usePartiesT } from "./i18n";

function contactArgs(
  t: ReturnType<typeof usePartiesT>,
  valueLabel: string,
  valuePlaceholder?: string,
): readonly ActionArg[] {
  return [
    {
      name: "value",
      label: valueLabel,
      ...(valuePlaceholder ? { placeholder: valuePlaceholder } : {}),
      description: t("party.contact.value.description"),
    },
    {
      name: "label",
      label: t("party.contact.label"),
      placeholder: t("party.contact.label.placeholder"),
      optional: true,
    },
  ];
}

/** Native email/phone actions shared by every saved Party-kind form. */
export function usePartyContactActions(): React.ReactElement {
  const t = usePartiesT();
  const [proposeManualContact] = useActionOutcomeMutation<ActionFieldName>(
    "propose_manual_contact",
    {
      idArgument: "party_id",
      invalidateModels: [...PARTY_STATEMENT_DECISION_INVALIDATES],
    },
  );
  const emailArgs = React.useMemo(
    () => contactArgs(t, t("party.contact.emailValue")),
    [t],
  );
  const phoneArgs = React.useMemo(
    () => contactArgs(
      t,
      t("party.contact.phoneValue"),
      t("party.contact.phone.placeholder"),
    ),
    [t],
  );
  const proposeContact = React.useCallback(
    async (
      platform: "email" | "phone",
      values: Record<string, unknown>,
      context: ActionFormContext,
      options?: AuthoredMutateOptions,
    ) => {
      const partyId = typeof context.record?.id === "string" ? context.record.id : "";
      const value = typeof values.value === "string" ? values.value.trim() : "";
      const label = typeof values.label === "string" ? values.label.trim() : "";
      if (!partyId || !value) {
        return {
          ok: false,
          message: t("party.contact.required"),
          validationErrors: { value: [t("party.contact.required")] },
        };
      }
      return (await proposeManualContact(partyId, { platform, value, label }, options)) ?? {
        ok: false,
        message: t("party.contact.error"),
      };
    },
    [proposeManualContact, t],
  );
  const submitEmail = React.useCallback<NonNullable<ActionDescriptor["submit"]>>(
    (values, context, options) => proposeContact("email", values, context, options),
    [proposeContact],
  );
  const submitPhone = React.useCallback<NonNullable<ActionDescriptor["submit"]>>(
    (values, context, options) => proposeContact("phone", values, context, options),
    [proposeContact],
  );
  const savedRecord = React.useCallback<NonNullable<ActionDescriptor["visibleWhen"]>>(
    (record) => String(record.id ?? "") !== REFINE_CREATE_ID,
    [],
  );

  return <>
    <Action
      id="add-email"
      label={t("party.contact.addEmail")}
      visibleWhen={savedRecord}
      args={emailArgs}
      submit={submitEmail}
    />
    <Action
      id="add-phone"
      label={t("party.contact.addPhone")}
      visibleWhen={savedRecord}
      args={phoneArgs}
      submit={submitPhone}
    />
  </>;
}
