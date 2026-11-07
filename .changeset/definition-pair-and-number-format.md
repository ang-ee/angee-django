---
"@angee/ui": minor
---

`DefinitionPair` owns the label/value pair; `MetaGrid`, `InfoRow` and
`MetricTile` compose it, and a column subline renders as its stacked detail.
`formatNumber` is the one numeric formatter (native `Intl` options,
BigInt-safe) for charts, stat, measures and the number and money widgets, and
dates and numbers format in the one app-language locale.
