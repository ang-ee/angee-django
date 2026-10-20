2026-09-11 — fed the Nexus V4 explorer ruling. Superseded by the Nexus InboxPage and its Navigator, MessageReader and Related surfaces, composing ConsoleLayout and shared resource views.

# Inbox Explorer study

V4 is a historical console composition study. Its Storybook entry is
`Explorations/2026-09-11 Inbox Explorer V4 — Nexus ruling`, with eight presets covering the
overview, conversation and message reading, search, Related uses, cross-account
reading, full conversation context, and suggested identity states.

The real `ConsoleLayout` owns the sender primary pane and Related secondary pane.
`ResourceList` inline placement owns the central list-to-message transition, and
the message reader keeps Related visible alongside it. Its local fixtures,
models and Related anatomy exist only to preserve this dated exploration;
production callers use the shipped Nexus and UI owners.

This remains a desktop fixture study.
