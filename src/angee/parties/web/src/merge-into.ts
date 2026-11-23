import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import { useRouteHref, type ActionDescriptor } from "@angee/ui";

import { usePartiesT } from "./i18n";

/**
 * "Merge into…": choose the other record, then open the compare page for the
 * pair (`parties.merge`), which owns the merge, its choices and its refusals.
 * The person form declares it as a record verb (`<Action {...mergeInto} />`),
 * offered only on a record whose projected permissions hold `merge_away`, what
 * the server asks of the record merged away; a list's selection of one record
 * opens it through `ActionFormDialog`, whose rows carry no projection, so there
 * the compare page refuses whoever may not merge.
 * Navigation goes through the router, whose app root keeps the current `?app=`.
 */
export function useMergeIntoAction(): ActionDescriptor {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  return React.useMemo<ActionDescriptor>(() => ({
    id: "merge-into",
    label: t("person.action.merge"),
    permission: "merge_away",
    args: [{
      name: "otherParty",
      argKind: "relation",
      resource: "parties.Party",
      label: t("person.action.mergeOther"),
      description: t("person.action.mergeOther.description"),
    }],
    submit: async (values, context) => {
      const recordId = typeof context.record?.id === "string" ? context.record.id : "";
      const otherId = typeof values.otherParty === "string" ? values.otherParty : "";
      if (!recordId || !otherId || recordId === otherId) {
        return {
          ok: false,
          message: t("person.action.mergeRequired"),
          validationErrors: { otherParty: [t("person.action.mergeRequired")] },
        };
      }
      await navigate({ to: routeHref("parties.merge", { left: recordId, right: otherId }) });
      return { ok: true, message: "" };
    },
  }), [navigate, routeHref, t]);
}
