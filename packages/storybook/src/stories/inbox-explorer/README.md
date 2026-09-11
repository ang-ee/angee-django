# Inbox Explorer study

V4 is the active console composition study. Its Storybook entry remains
`Nexus / Inbox Explorer V4 — console study`, with eight presets covering the
overview, conversation and message reading, search, Related uses, cross-account
reading, full conversation context, and suggested identity states.

The real `ConsoleLayout` owns the sender primary pane and Related secondary pane.
`ResourceList` inline placement owns the central list-to-message transition, and
the message reader keeps Related visible alongside it. `fixtures.ts`,
`v2-model.ts`, `v3-model.ts`, and `Related.tsx` remain live because V4 consumes
them.

V1–V3, including their Storybook entries, component sources, CSS, fixtures,
models, and the full study notes that accompanied them, are preserved in the
[private study archive](../../../../../../.work/plans/specs/nexus-inbox-explorer/archive/2026-09-11-studies/).

This remains a desktop fixture study. Production requirements and ownership
notes live under the
[Nexus Inbox Explorer specification](../../../../../../.work/plans/specs/nexus-inbox-explorer/).
