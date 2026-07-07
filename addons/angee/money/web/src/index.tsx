import { defineBaseAddon } from "@angee/app";

import { moneyWidget } from "./widgets/money";

const MONEY_ID = "money";

/**
 * The `@angee/money` rendered addon. It contributes exactly one thing: the
 * renderer for the backend-owned `"money"` widget key (a MoneyField projects
 * `widget: "money"` in its resource metadata). No pages — currencies and rates
 * are administered through the generic console resource pages; this package only
 * teaches the UI how to render a money column/field.
 */
const money = defineBaseAddon({
  id: MONEY_ID,
  widgets: {
    money: moneyWidget,
  },
});

export default money;
