---
"@angee/refine": patch
"@angee/ui": patch
"@angee/app": patch
---

Use the shared authored-query lifecycle for aggregates, group batches and dashboard polling, and enforce transient mutation single-flight execution. Concurrent calls involving a transient mutation reject before dispatch.

Compose action arguments through descriptor fields and relation selection through the shared Combobox. Bundle the headless appearance owner for first paint so cached tokens use the same validation as React.

Unify Storybook fixture providers and routing codecs. Remove the obsolete composition report and its unused catalog-schema exports.
