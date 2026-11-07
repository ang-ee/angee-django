---
"@angee/ui": major
---

Typography and motion are theme tokens read through the Tailwind bridges:
weight, size, chart and motion tokens are exposed to themes, transitions take
their duration and easing from `--dur-base` and `--ease`, and `useMotionTokens`
reads them. Display and value text roles style titles and numeric reads.
`MetricTile` takes `valueSize` instead of `valueClassName` and animates a typed
numeric value with number-flow (new dependency `@number-flow/react`).
