import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";

import { CurrenciesPage, CurrencyRatesPage } from "./views/MoneyPages";
import { moneyWidget } from "./widgets/money";

const MONEY_ID = "money";

const moneyRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "money.currencies",
    "/money/currencies",
    CurrenciesPage,
    "money.Currency",
    { detailName: "money.currency" },
  ),
  ...resourcePageRoutes(
    "money.rates",
    "/money/rates",
    CurrencyRatesPage,
    "money.CurrencyRate",
    { detailName: "money.rate" },
  ),
];

const moneyMenu: readonly BaseMenuItem[] = [
  {
    id: MONEY_ID,
    label: "Money",
    icon: "archive",
    sidebar: true,
    children: [
      {
        id: "money.currencies",
        label: "Currencies",
        icon: "grid",
        route: "money.currencies",
      },
      {
        id: "money.rates",
        label: "Rates",
        icon: "activity",
        route: "money.rates",
      },
    ],
  },
];

/**
 * The `@angee/money` rendered addon. It contributes the currency/rate
 * administration pages and the renderer for the backend-owned `"money"` widget
 * key (a MoneyField projects `widget: "money"` in its resource metadata).
 */
const money = defineBaseAddon({
  id: MONEY_ID,
  routes: moneyRoutes,
  menus: moneyMenu,
  widgets: {
    money: moneyWidget,
  },
});

export default money;
