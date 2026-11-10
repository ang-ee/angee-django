---
"@angee/ui": patch
---

`createUiTestProviders` opens a hovered tooltip in the same event
(`TooltipProvider delay={0}`) instead of after Base UI's 600 ms open delay, so a
hover assertion no longer races the clock. A nested `TooltipProvider` keeps its
own delay.
